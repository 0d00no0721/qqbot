#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 指令处理 — 解析用户输入，调用 market/account/engine/events 执行交易或查询。

指令列表（无前缀，与现有机器人指令风格一致）：
    行情              — 查看全部股票当前价格
    股票 <代码或名称>  — 查看单支股票详情
    买入 <代码或名称> <数量> [杠杆]  — 做多买入（杠杆可选 2 或 3）
    卖出 <代码或名称> <数量>         — 卖出平多
    做空 <代码或名称> <数量>         — 融券做空
    平空 <代码或名称> <数量>         — 买回平空
    持仓              — 查看自己的持仓
    账户              — 查看账户余额和总资产
    体力              — 查看交易体力值
    富豪榜            — 查看富豪榜/负豪榜
    破产恢复          — 申请破产救济
    股市帮助          — 显示帮助信息
"""

from typing import Optional

from .data import STOCK_CODES, DEFAULT_STOCK_NAMES
from .engine import get_stock_info, get_all_stocks
from .market import (
    buy_long, sell_long, sell_short, cover_short,
    get_ask_price, get_bid_price,
)
from .account import (
    get_or_create_account, get_total_assets, get_margin_ratio,
)
from .events import process_bankruptcy_recovery, generate_leaderboard
from .risk import is_trading_halted


# ========== 指令识别 ==========

# 所有虚拟股指令关键词（按字数降序排列，避免短词误匹配）
VS_COMMANDS = [
    "破产恢复", "股市帮助", "富豪榜",
    "行情", "股票", "买入", "卖出", "做空", "平空",
    "持仓", "账户", "体力",
]


def is_vs_command(text: str) -> bool:
    """快速判断一段文本是否以虚拟股指令开头。"""
    text = text.strip()
    for cmd in VS_COMMANDS:
        if text == cmd or text.startswith(cmd + " "):
            return True
    return False


# ========== 股票名称 ↔ 代码解析 ==========

def resolve_stock_code(text: str) -> Optional[str]:
    """将用户输入解析为股票代码。支持代码和名称。"""
    text = text.strip()
    # 直接匹配代码
    if text in STOCK_CODES:
        return text
    # 匹配名称
    for code, name in DEFAULT_STOCK_NAMES.items():
        if text == name:
            return code
    return None


# ========== 主分发函数 ==========

def handle_vs_command(group_id: str, user_id: str, text: str) -> Optional[str]:
    """
    处理虚拟股指令。
    返回回复文本（str），或 None 表示不是虚拟股指令。

    参数：
        group_id: 群号
        user_id:  用户 QQ 号
        text:     去掉 @bot 后的纯文本指令
    """
    text = text.strip()
    if not is_vs_command(text):
        return None

    group_id = str(group_id)
    user_id = str(user_id)

    try:
        if text == "行情":
            return _cmd_market(group_id)
        elif text.startswith("股票"):
            return _cmd_stock_info(group_id, text)
        elif text.startswith("买入"):
            return _cmd_buy(group_id, user_id, text)
        elif text.startswith("卖出"):
            return _cmd_sell(group_id, user_id, text)
        elif text.startswith("做空"):
            return _cmd_short(group_id, user_id, text)
        elif text.startswith("平空"):
            return _cmd_cover(group_id, user_id, text)
        elif text == "持仓":
            return _cmd_portfolio(group_id, user_id)
        elif text == "账户":
            return _cmd_account(group_id, user_id)
        elif text == "体力":
            return _cmd_stamina(group_id, user_id)
        elif text == "富豪榜":
            return _cmd_leaderboard(group_id)
        elif text == "破产恢复":
            return process_bankruptcy_recovery(group_id, user_id)
        elif text == "股市帮助":
            return _cmd_help()
    except Exception as e:
        return f"❌ 指令处理出错: {e}"

    return None


# ========== 查询类指令 ==========

def _cmd_market(group_id: str) -> str:
    """行情：显示全部股票当前价格、涨跌幅、停牌状态。"""
    stocks = get_all_stocks(group_id)

    from .data import load_prices
    price_data = load_prices(group_id)
    prev_close = price_data.get("prev_close", {})

    lines = ["📈 **【虚拟股市行情】**"]
    lines.append("")

    for s in stocks:
        code = s["code"]
        name = s["name"]
        price = s["price"]
        ath = s["all_time_high"]
        prev = float(prev_close.get(code, price))
        if prev > 0:
            pct = (price - prev) / prev * 100
            pct_str = f"{pct:+.2f}%"
        else:
            pct_str = "  --"

        # 停牌状态
        halted, halt_msg = is_trading_halted(group_id, code)
        status = "⛔停牌" if halted else ""

        lines.append(
            f"  {code} {name}\n"
            f"    现价: {price:.2f}  涨跌: {pct_str}  峰值: {ath:.2f} {status}"
        )

    lines.append("")
    lines.append("  买一价 = 现价 × 1.005  卖一价 = 现价 × 0.995")
    lines.append("  指令：股票 <名称> | 买入 <名称> <数量> [杠杆] | 股市帮助")

    return "\n".join(lines)


def _cmd_stock_info(group_id: str, text: str) -> str:
    """股票：查看单支股票详情。"""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return "❌ 格式：股票 <代码或名称>\n例如：股票 群主控股  或  股票 600001"

    code = resolve_stock_code(parts[1].strip())
    if code is None:
        return f"❌ 未找到股票：{parts[1].strip()}\n可用股票代码：{', '.join(STOCK_CODES)}"

    info = get_stock_info(group_id, code)
    name = info["name"]
    price = info["price"]
    ath = info["all_time_high"]
    ask = get_ask_price(group_id, code)
    bid = get_bid_price(group_id, code)

    halted, halt_msg = is_trading_halted(group_id, code)

    from .data import load_prices
    price_data = load_prices(group_id)
    prev = float(price_data.get("prev_close", {}).get(code, price))
    if prev > 0:
        pct = (price - prev) / prev * 100
        pct_str = f"{pct:+.2f}%"
    else:
        pct_str = "  --"

    lines = [
        f"📊 **【{name}】**（{code}）",
        f"  现价: {price:.2f}  涨跌: {pct_str}",
        f"  买一价: {ask:.2f}  卖一价: {bid:.2f}",
        f"  历史峰值: {ath:.2f}",
        f"  总发行量: {info['total_shares']} 股",
    ]

    if halted:
        lines.append(f"  ⛔ 当前停牌：{halt_msg}")
    else:
        lines.append("  ✅ 正常交易中")

    return "\n".join(lines)


# ========== 交易类指令 ==========

def _cmd_buy(group_id: str, user_id: str, text: str) -> str:
    """买入 <代码或名称> <数量> [杠杆]"""
    parts = text.split()
    if len(parts) < 3:
        return "❌ 格式：买入 <代码或名称> <数量> [杠杆]\n例如：买入 群主控股 100  或  买入 600001 100 3"

    code = resolve_stock_code(parts[1])
    if code is None:
        return f"❌ 未找到股票：{parts[1]}"

    try:
        quantity = int(parts[2])
    except ValueError:
        return f"❌ 数量必须是整数：{parts[2]}"

    leverage = 1
    if len(parts) >= 4:
        try:
            leverage = int(parts[3])
        except ValueError:
            return f"❌ 杠杆倍数必须是整数：{parts[3]}"

    result = buy_long(group_id, user_id, code, quantity, leverage)
    return result.message


def _cmd_sell(group_id: str, user_id: str, text: str) -> str:
    """卖出 <代码或名称> <数量>"""
    parts = text.split()
    if len(parts) < 3:
        return "❌ 格式：卖出 <代码或名称> <数量>\n例如：卖出 水群地产 50"

    code = resolve_stock_code(parts[1])
    if code is None:
        return f"❌ 未找到股票：{parts[1]}"

    try:
        quantity = int(parts[2])
    except ValueError:
        return f"❌ 数量必须是整数：{parts[2]}"

    result = sell_long(group_id, user_id, code, quantity)
    return result.message


def _cmd_short(group_id: str, user_id: str, text: str) -> str:
    """做空 <代码或名称> <数量>"""
    parts = text.split()
    if len(parts) < 3:
        return "❌ 格式：做空 <代码或名称> <数量>\n例如：做空 战雷航空 30"

    code = resolve_stock_code(parts[1])
    if code is None:
        return f"❌ 未找到股票：{parts[1]}"

    try:
        quantity = int(parts[2])
    except ValueError:
        return f"❌ 数量必须是整数：{parts[2]}"

    result = sell_short(group_id, user_id, code, quantity)
    return result.message


def _cmd_cover(group_id: str, user_id: str, text: str) -> str:
    """平空 <代码或名称> <数量>"""
    parts = text.split()
    if len(parts) < 3:
        return "❌ 格式：平空 <代码或名称> <数量>\n例如：平空 100001 30"

    code = resolve_stock_code(parts[1])
    if code is None:
        return f"❌ 未找到股票：{parts[1]}"

    try:
        quantity = int(parts[2])
    except ValueError:
        return f"❌ 数量必须是整数：{parts[2]}"

    result = cover_short(group_id, user_id, code, quantity)
    return result.message


# ========== 账户类指令 ==========

def _cmd_portfolio(group_id: str, user_id: str) -> str:
    """持仓：查看自己的持仓详情。"""
    account = get_or_create_account(user_id, group_id)
    positions = account.get("positions", {})
    liabilities = account.get("liabilities", {})

    lines = [f"📋 **【{user_id} 的持仓】**"]

    has_position = False
    for code, pos in positions.items():
        if pos.get("quantity", 0) > 0:
            has_position = True
            name = DEFAULT_STOCK_NAMES.get(code, code)
            qty = pos["quantity"]
            avg_cost = pos.get("avg_cost", 0)
            leverage = pos.get("leverage", 1)
            # 当前市值
            info = get_stock_info(group_id, code)
            cur_price = info["price"]
            market_val = qty * cur_price
            pnl = (cur_price - avg_cost) * qty
            pnl_pct = ((cur_price - avg_cost) / avg_cost * 100) if avg_cost > 0 else 0

            lev_str = f" [{leverage}x杠杆]" if leverage > 1 else ""
            lines.append(
                f"  {code} {name}{lev_str}\n"
                f"    持仓: {qty}股  成本: {avg_cost:.2f}  现价: {cur_price:.2f}\n"
                f"    市值: {market_val:.2f}  盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%)"
            )

    has_liability = False
    for code, liab in liabilities.items():
        if liab.get("quantity", 0) > 0:
            has_liability = True
            name = DEFAULT_STOCK_NAMES.get(code, code)
            qty = liab["quantity"]
            borrow_price = liab.get("borrow_price", 0)
            info = get_stock_info(group_id, code)
            cur_price = info["price"]
            pnl = (borrow_price - cur_price) * qty
            pnl_pct = ((borrow_price - cur_price) / borrow_price * 100) if borrow_price > 0 else 0

            lines.append(
                f"  {code} {name} [做空]\n"
                f"    借入: {qty}股  借入价: {borrow_price:.2f}  现价: {cur_price:.2f}\n"
                f"    盈亏: {pnl:+.2f} ({pnl_pct:+.2f}%)"
            )

    if not has_position and not has_liability:
        lines.append("  （空仓）")

    return "\n".join(lines)


def _cmd_account(group_id: str, user_id: str) -> str:
    """账户：查看余额和总资产。"""
    account = get_or_create_account(user_id, group_id)
    balance = account.get("balance", 0)
    total = get_total_assets(account, group_id)

    # 计算持仓市值和负债
    positions = account.get("positions", {})
    liabilities = account.get("liabilities", {})
    long_val = 0
    short_val = 0
    for code, pos in positions.items():
        if pos.get("quantity", 0) > 0:
            info = get_stock_info(group_id, code)
            long_val += pos["quantity"] * info["price"]
    for code, liab in liabilities.items():
        if liab.get("quantity", 0) > 0:
            info = get_stock_info(group_id, code)
            short_val += liab["quantity"] * info["price"]

    lines = [
        f"💰 **【{user_id} 的账户】**",
        f"  现金: {balance:.2f}",
        f"  多头市值: {long_val:.2f}",
        f"  空头负债: {short_val:.2f}",
        f"  ────────────",
        f"  总资产: {total:.2f}",
    ]

    # 保证金率（有杠杆持仓时显示）
    margin = get_margin_ratio(account, group_id)
    if margin is not None:
        lines.append(f"  保证金率: {margin * 100:.1f}%")
        if margin <= 0.15:
            lines.append("  ⚠️ 保证金率低于 15%，存在爆仓风险！")

    return "\n".join(lines)


def _cmd_stamina(group_id: str, user_id: str) -> str:
    """体力：查看交易体力值。"""
    account = get_or_create_account(user_id, group_id)
    stamina = account.get("stamina", 10)

    from .data import load_group_config
    config = load_group_config(group_id)
    max_stamina = config.get("stamina_max", 10)
    interval = config.get("stamina_recover_interval", 1800)

    lines = [
        f"⚡ **【交易体力值】**",
        f"  当前: {stamina} / {max_stamina}",
    ]

    if stamina < max_stamina:
        next_recover = interval // 60
        lines.append(f"  每 {next_recover} 分钟恢复 1 点")
    else:
        lines.append("  ✅ 体力已满")

    return "\n".join(lines)


def _cmd_leaderboard(group_id: str) -> str:
    """富豪榜：查看富豪榜和负豪榜。"""
    result = generate_leaderboard(group_id)
    return result.message


def _cmd_help() -> str:
    """股市帮助：显示帮助信息。"""
    lines = [
        "📖 **【虚拟股市帮助】**",
        "",
        "**查询指令：**",
        "  行情              — 查看全部股票行情",
        "  股票 <名称或代码>  — 查看单支股票详情",
        "  持仓              — 查看自己的持仓",
        "  账户              — 查看余额和总资产",
        "  体力              — 查看交易体力值",
        "  富豪榜            — 查看富豪榜/负豪榜",
        "",
        "**交易指令：**",
        "  买入 <名称或代码> <数量> [杠杆]  — 做多买入",
        "  卖出 <名称或代码> <数量>         — 卖出平多",
        "  做空 <名称或代码> <数量>         — 融券做空",
        "  平空 <名称或代码> <数量>         — 买回平空",
        "",
        "**其他：**",
        "  破产恢复  — 总资产低于 50 金币时可申请救济",
        "  股市帮助  — 显示本帮助",
        "",
        "**股票列表：**",
    ]

    for code, name in DEFAULT_STOCK_NAMES.items():
        lines.append(f"  {code} {name}")

    lines.append("")
    lines.append("**交易规则：**")
    lines.append("  • 买卖价差 1%（买价 = 现价×1.005, 卖价 = 现价×0.995）")
    lines.append("  • 每次交易消耗 1 点体力，30 分钟恢复 1 点，上限 10 点")
    lines.append("  • 杠杆可选 2 倍或 3 倍，保证金率 ≤ 10% 触发强平")
    lines.append("  • 单股 1 小时涨跌超 30% 或大盘跌超 15% 触发熔断")
    lines.append("  • 股价 ≥ 1000 自动 1:10 拆股")
    lines.append("  • 每周按历史峰值分红")

    return "\n".join(lines)
