#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 事件系统 — 拆股、分红、破产恢复、每日收盘、富豪榜。
所有事件由 scheduler 定时触发或由用户指令触发。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .data import (
    STOCK_CODES, DEFAULT_STOCK_NAMES,
    load_group_config, load_prices, save_prices,
    load_accounts, save_accounts,
    load_ecosystem_fund, save_ecosystem_fund,
)
from .account import (
    get_or_create_account, get_total_assets,
    add_balance, apply_bankruptcy_recovery,
)


# ========== 数据结构 ==========

@dataclass
class SplitEvent:
    """拆股事件。"""
    stock_code: str
    stock_name: str
    old_price: float
    new_price: float
    split_ratio: int
    message: str


@dataclass
class DividendEvent:
    """分红事件。"""
    stock_code: str
    stock_name: str
    total_dividend: float
    recipients: int
    message: str


@dataclass
class DailyReport:
    """每日收盘报告。"""
    date: str
    top_gainer: Optional[Dict[str, Any]]
    top_loser: Optional[Dict[str, Any]]
    message: str


@dataclass
class LeaderboardData:
    """富豪榜数据。"""
    richest: List[Dict[str, Any]]
    poorest: List[Dict[str, Any]]
    message: str


# ========== 拆股 ==========

def check_stock_split(group_id: str, stock_code: str) -> Optional[SplitEvent]:
    """
    检查某股票是否触发拆股条件（股价 ≥ 阈值）。
    触发则执行 1:10 拆股并返回事件，否则返回 None。
    """
    config = load_group_config(group_id)
    threshold = config.get("split_threshold", 1000.0)
    ratio = config.get("split_ratio", 10)

    prices = load_prices(group_id)
    current = prices.get("current", {})
    old_price = float(current.get(stock_code, 0.0))

    if old_price < threshold:
        return None

    new_price = round(old_price / ratio, 2)
    current[stock_code] = new_price

    # 更新历史峰值（拆股后峰值也相应降低）
    all_time_high = prices.get("all_time_high", {})
    if stock_code in all_time_high:
        all_time_high[stock_code] = round(all_time_high[stock_code] / ratio, 2)

    save_prices(group_id, prices)

    # 更新所有持股用户的持仓数量
    accounts = load_accounts(group_id)
    for user_id, account in accounts.items():
        positions = account.get("positions", {})
        pos = positions.get(stock_code)
        if pos:
            pos["quantity"] = pos.get("quantity", 0) * ratio
            pos["leveraged_quantity"] = pos.get("leveraged_quantity", 0) * ratio
            pos["avg_cost"] = round(pos.get("avg_cost", 0.0) / ratio, 4)
            # 债务也按比例拆分
            pos["debt"] = round(pos.get("debt", 0.0) / ratio, 2)
    save_accounts(group_id, accounts)

    stock_name = DEFAULT_STOCK_NAMES.get(stock_code, stock_code)
    msg = (
        f"📊 **【拆股公告】** {stock_name}（{stock_code}）"
        f"股价已达 {old_price:.2f} 金币，触发 1:{ratio} 拆股！\n"
        f"股价调整为 {new_price:.2f}，持股数量 ×{ratio}。"
        f"总资产价值不变。"
    )

    return SplitEvent(
        stock_code=stock_code,
        stock_name=stock_name,
        old_price=old_price,
        new_price=new_price,
        split_ratio=ratio,
        message=msg,
    )


# ========== 分红 ==========

def process_weekly_dividend(group_id: str) -> List[DividendEvent]:
    """
    每周日 22:00 执行：检查各股票本周指标是否创历史新高。
    若创新高，从生态发展基金中按总市值 × 股息率拨出分红，
    按持股比例派发给持股用户。
    """
    config = load_group_config(group_id)
    dividend_rate = config.get("dividend_rate", 0.0005)
    fund = load_ecosystem_fund()

    prices = load_prices(group_id)
    current = prices.get("current", {})
    all_time_high = prices.get("all_time_high", {})

    accounts = load_accounts(group_id)
    events: List[DividendEvent] = []

    for code in STOCK_CODES:
        price = float(current.get(code, 0.0))
        # 检查是否本周创新高（当前价 == 历史最高）
        ath = float(all_time_high.get(code, 0.0))
        if price < ath or price <= 0:
            continue

        # 计算分红总额 = 总市值 × 股息率
        total_shares = config["stocks"][code]["total_shares"]
        market_cap = total_shares * price
        total_dividend = round(market_cap * dividend_rate, 2)

        if fund < total_dividend:
            # 基金不足，按基金余额全额派发
            total_dividend = round(fund, 2)
        if total_dividend <= 0:
            continue

        # 统计所有持股用户
        holders = {}
        total_held = 0
        for user_id, account in accounts.items():
            pos = account.get("positions", {}).get(code)
            if pos:
                qty = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
                if qty > 0:
                    holders[user_id] = qty
                    total_held += qty

        if total_held <= 0:
            continue

        # 按比例派发
        for user_id, qty in holders.items():
            share = round(total_dividend * qty / total_held, 2)
            if share > 0:
                accounts[user_id]["balance"] = round(
                    accounts[user_id].get("balance", 0.0) + share, 2
                )

        # 从基金扣除
        fund -= total_dividend
        save_ecosystem_fund(fund)

        stock_name = DEFAULT_STOCK_NAMES.get(code, code)
        msg = (
            f"💰 **【分红公告】** {stock_name}（{code}）"
            f"本周指标创历史新高！\n"
            f"分红总额: {total_dividend:.2f} 金币\n"
            f"按持股比例派发给 {len(holders)} 位股东。"
        )
        events.append(DividendEvent(
            stock_code=code,
            stock_name=stock_name,
            total_dividend=total_dividend,
            recipients=len(holders),
            message=msg,
        ))

    if events:
        save_accounts(group_id, accounts)

    return events


# ========== 每日收盘 & 富豪榜 ==========

def process_daily_close(group_id: str) -> DailyReport:
    """
    每日 23:30 收盘：
    1. 记录各股票今日收盘价（写入 history）
    2. 重置当日指标缓冲
    3. 计算涨跌幅
    返回收盘报告。
    """
    prices = load_prices(group_id)
    current = prices.get("current", {})
    prev_close = prices.get("prev_close", {})

    # 记录今日涨跌幅
    top_gainer = None
    top_loser = None
    best_pct = -999.0
    worst_pct = 999.0

    for code in STOCK_CODES:
        curr = float(current.get(code, 0.0))
        prev = float(prev_close.get(code, curr))
        if prev > 0:
            pct = (curr - prev) / prev * 100
        else:
            pct = 0.0

        stock_name = DEFAULT_STOCK_NAMES.get(code, code)
        info = {"code": code, "name": stock_name, "price": curr,
                "prev": prev, "pct": round(pct, 2)}

        if pct > best_pct:
            best_pct = pct
            top_gainer = info
        if pct < worst_pct:
            worst_pct = pct
            top_loser = info

    # 更新 prev_close 为当前价
    prices["prev_close"] = dict(current)

    # 写入历史记录
    history = prices.get("history", {})
    today = datetime.now().strftime("%Y-%m-%d")
    history[today] = dict(current)
    prices["history"] = history

    save_prices(group_id, prices)

    # 组装报告
    lines = [f"📈 **【每日收盘报告 {today}】**"]
    if top_gainer:
        lines.append(
            f"  🟢 涨幅冠军: {top_gainer['name']}（{top_gainer['code']}）"
            f"  {top_gainer['pct']:+.2f}%  → {top_gainer['price']:.2f}"
        )
    if top_loser:
        lines.append(
            f"  🔴 跌幅冠军: {top_loser['name']}（{top_loser['code']}）"
            f"  {top_loser['pct']:+.2f}%  → {top_loser['price']:.2f}"
        )
    lines.append("  收盘！明日 00:05 发布富豪榜。")

    return DailyReport(
        date=today,
        top_gainer=top_gainer,
        top_loser=top_loser,
        message="\n".join(lines),
    )


# ========== 富豪榜 ==========

def generate_leaderboard(group_id: str, top_n: int = 5) -> LeaderboardData:
    """
    凌晨 00:05 发布：计算所有用户总资产，排出富豪榜 TOP N 和负豪榜 BOTTOM N。
    总资产 = 现金 + 持仓市值 - 做空债务。
    """
    prices = load_prices(group_id)
    current = prices.get("current", {})
    accounts = load_accounts(group_id)

    user_assets: List[Dict[str, Any]] = []

    for user_id, account in accounts.items():
        total = get_total_assets(account, group_id)
        user_assets.append({
            "user_id": user_id,
            "total": round(total, 2),
        })

    # 按总资产排序
    user_assets.sort(key=lambda x: x["total"], reverse=True)

    richest = user_assets[:top_n]
    poorest = list(reversed(user_assets[-top_n:])) if len(user_assets) >= top_n else list(reversed(user_assets))

    # 组装消息
    lines = ["🏆 **【每日富豪榜】**"]
    for i, u in enumerate(richest, 1):
        lines.append(f"  {i}. <@{u['user_id']}>  💰 {u['total']:.2f}")

    lines.append("")
    lines.append("💀 **【负豪榜】**")
    for i, u in enumerate(poorest, 1):
        lines.append(f"  {i}. <@{u['user_id']}>  💸 {u['total']:.2f}")

    lines.append("")
    lines.append("  总资产 < 50 可使用 #破产恢复 领取救济金（每日限1次）。")

    return LeaderboardData(
        richest=richest,
        poorest=poorest,
        message="\n".join(lines),
    )


# ========== 破产恢复 ==========

def process_bankruptcy_recovery(group_id: str, user_id: str) -> str:
    """
    破产恢复：总资产 < 50 金币时可申请，恢复到 200 金币，每日限 1 次。
    直接委托给 account.apply_bankruptcy_recovery，保持逻辑唯一。
    """
    return apply_bankruptcy_recovery(group_id, user_id)