#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 账户系统 — 金币、持仓、保证金、杠杆、负债管理。

账户数据模型：
    Account = {
        "user_id": str,
        "group_id": str,
        "balance": float,                    # 可用金币
        "frozen_balance": float,             # 冻结保证金
        "positions": {code: Position},       # 持仓
        "liabilities": {code: Liability},    # 做空负债
        "stamina": int,                      # 交易体力值
        "stamina_updated_at": str,           # 体力最后更新时间 ISO
        "total_trade_count": int,
        "bankruptcy_used_today": bool,
        "no_leverage_until": str | None,     # 破产后禁杠杆截止时间 ISO
    }

    Position = {
        "stock_code": str,
        "quantity": int,                     # 自有资金买入的股数
        "avg_cost": float,                   # 平均成本价
        "leveraged_quantity": int,           # 杠杆买入的股数
        "leverage_multiplier": int,          # 杠杆倍数 (1~3)
        "debt": float,                       # 欠庄家金币数（杠杆借款本金）
    }

    Liability = {
        "stock_code": str,
        "short_quantity": int,               # 融券卖出的股数
        "short_price": float,                # 做空开仓价
        "frozen_margin": float,              # 冻结的保证金
    }
"""

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from .data import (
    load_account, save_account, load_accounts, save_accounts,
    locked_accounts,
    load_group_config, load_prices, STOCK_CODES,
)


# ========== 账户创建与查询 ==========

def _make_default_account(user_id: str, group_id: str) -> Dict[str, Any]:
    """创建默认新账户。"""
    config = load_group_config(group_id)
    return {
        "user_id": str(user_id),
        "group_id": str(group_id),
        "balance": config.get("initial_balance", 1000.0),
        "frozen_balance": 0.0,
        "positions": {},
        "liabilities": {},
        "stamina": config.get("stamina_max", 10),
        "stamina_updated_at": datetime.now().isoformat(),
        "total_trade_count": 0,
        "bankruptcy_used_today": False,
        "no_leverage_until": None,
    }


def get_or_create_account(user_id: str, group_id: str) -> Dict[str, Any]:
    """获取账户，不存在则自动创建。"""
    account = load_account(group_id, user_id)
    if account is None:
        account = _make_default_account(user_id, group_id)
        save_account(group_id, user_id, account)
    return account


def get_account(user_id: str, group_id: str) -> Optional[Dict[str, Any]]:
    """获取账户，不存在返回 None。"""
    return load_account(group_id, user_id)


# ========== 余额操作 ==========

def add_balance(group_id: str, user_id: str, amount: float) -> float:
    """增加可用余额，返回新的 balance。"""
    account = get_or_create_account(user_id, group_id)
    account["balance"] = round(account["balance"] + amount, 2)
    save_account(group_id, user_id, account)
    return account["balance"]


def deduct_balance(group_id: str, user_id: str, amount: float) -> Tuple[bool, float]:
    """
    扣除可用余额。返回 (成功?, 剩余余额)。
    余额不足时不扣除，返回 (False, 当前余额)。
    """
    account = get_or_create_account(user_id, group_id)
    if account["balance"] < amount:
        return False, account["balance"]
    account["balance"] = round(account["balance"] - amount, 2)
    save_account(group_id, user_id, account)
    return True, account["balance"]


def freeze_balance(group_id: str, user_id: str, amount: float) -> bool:
    """冻结一部分余额作为保证金。余额不足返回 False。"""
    account = get_or_create_account(user_id, group_id)
    if account["balance"] < amount:
        return False
    account["balance"] = round(account["balance"] - amount, 2)
    account["frozen_balance"] = round(account["frozen_balance"] + amount, 2)
    save_account(group_id, user_id, account)
    return True


def unfreeze_balance(group_id: str, user_id: str, amount: float) -> None:
    """解冻保证金回到可用余额。"""
    account = get_or_create_account(user_id, group_id)
    release = min(amount, account["frozen_balance"])
    account["frozen_balance"] = round(account["frozen_balance"] - release, 2)
    account["balance"] = round(account["balance"] + release, 2)
    save_account(group_id, user_id, account)


# ========== 持仓操作 ==========

def get_position(account: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
    """获取某股票的持仓，不存在返回 None。"""
    return account.get("positions", {}).get(stock_code)


def add_position(
    group_id: str, user_id: str, stock_code: str,
    quantity: int, price: float, leveraged: int = 0,
    leverage_multiplier: int = 1, debt: float = 0.0,
) -> Dict[str, Any]:
    """
    增加持仓。若已有该股票持仓则合并（更新平均成本和数量）。
    返回更新后的 position。
    """
    account = get_or_create_account(user_id, group_id)
    positions = account.setdefault("positions", {})
    pos = positions.get(stock_code)

    if pos is None:
        pos = {
            "stock_code": stock_code,
            "quantity": 0,
            "avg_cost": 0.0,
            "leveraged_quantity": 0,
            "leverage_multiplier": leverage_multiplier,
            "debt": 0.0,
        }
        positions[stock_code] = pos

    # 合并持仓：加权平均成本
    total_qty = pos["quantity"] + quantity
    total_lev = pos["leveraged_quantity"] + leveraged
    if total_qty > 0:
        pos["avg_cost"] = round(
            (pos["avg_cost"] * pos["quantity"] + price * quantity) / total_qty, 4
        )
    pos["quantity"] = total_qty
    pos["leveraged_quantity"] = total_lev
    pos["debt"] = round(pos.get("debt", 0.0) + debt, 2)
    if leveraged > 0:
        pos["leverage_multiplier"] = leverage_multiplier

    account["total_trade_count"] = account.get("total_trade_count", 0) + 1
    save_account(group_id, user_id, account)
    return pos


def remove_position(
    group_id: str, user_id: str, stock_code: str,
    quantity: int, leveraged: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    减少持仓（卖出/平仓）。先减杠杆部分，再减自有部分。
    返回更新后的 position；若全部清空则删除该 key 并返回 None。
    """
    account = get_or_create_account(user_id, group_id)
    positions = account.get("positions", {})
    pos = positions.get(stock_code)

    if pos is None:
        return None

    # 先减杠杆股
    lev_to_remove = min(leveraged, pos.get("leveraged_quantity", 0))
    pos["leveraged_quantity"] -= lev_to_remove
    leveraged -= lev_to_remove

    # 剩余的从自有股扣除
    pos["quantity"] -= quantity
    if pos["quantity"] < 0:
        pos["quantity"] = 0

    # 若全部清空
    if pos["quantity"] <= 0 and pos["leveraged_quantity"] <= 0:
        del positions[stock_code]
        account["total_trade_count"] = account.get("total_trade_count", 0) + 1
        save_account(group_id, user_id, account)
        return None

    # 杠杆清空后重置 multiplier
    if pos["leveraged_quantity"] <= 0:
        pos["leverage_multiplier"] = 1
        pos["debt"] = 0.0

    account["total_trade_count"] = account.get("total_trade_count", 0) + 1
    save_account(group_id, user_id, account)
    return pos


def update_position_debt(
    group_id: str, user_id: str, stock_code: str, debt_change: float,
) -> None:
    """更新杠杆负债（利息扣除等）。"""
    account = get_or_create_account(user_id, group_id)
    pos = account.get("positions", {}).get(stock_code)
    if pos:
        pos["debt"] = round(pos.get("debt", 0.0) + debt_change, 2)
        save_account(group_id, user_id, account)


# ========== 做空负债管理 ==========

def add_liability(
    group_id: str, user_id: str, stock_code: str,
    quantity: int, price: float, margin: float,
) -> Dict[str, Any]:
    """添加做空负债（融券卖出）。"""
    account = get_or_create_account(user_id, group_id)
    liabilities = account.setdefault("liabilities", {})
    liab = liabilities.get(stock_code)

    if liab is None:
        liab = {
            "stock_code": stock_code,
            "short_quantity": 0,
            "short_price": 0.0,
            "frozen_margin": 0.0,
        }
        liabilities[stock_code] = liab

    # 合并做空持仓：加权平均开仓价
    total_qty = liab["short_quantity"] + quantity
    liab["short_price"] = round(
        (liab["short_price"] * liab["short_quantity"] + price * quantity) / total_qty, 4
    )
    liab["short_quantity"] = total_qty
    liab["frozen_margin"] = round(liab["frozen_margin"] + margin, 2)

    account["total_trade_count"] = account.get("total_trade_count", 0) + 1
    save_account(group_id, user_id, account)
    return liab


def remove_liability(
    group_id: str, user_id: str, stock_code: str, quantity: int,
) -> Optional[Dict[str, Any]]:
    """减少做空负债（买回平空）。全部清空返回 None。"""
    account = get_or_create_account(user_id, group_id)
    liabilities = account.get("liabilities", {})
    liab = liabilities.get(stock_code)

    if liab is None:
        return None

    liab["short_quantity"] -= quantity
    if liab["short_quantity"] <= 0:
        # 释放剩余保证金
        account["frozen_balance"] = round(
            account["frozen_balance"] - liab["frozen_margin"], 2
        )
        account["balance"] = round(
            account["balance"] + liab["frozen_margin"], 2
        )
        del liabilities[stock_code]
        save_account(group_id, user_id, account)
        return None

    # 按比例释放部分保证金
    release = round(liab["frozen_margin"] * quantity / (liab["short_quantity"] + quantity), 2)
    liab["frozen_margin"] = round(liab["frozen_margin"] - release, 2)
    account["frozen_balance"] = round(account["frozen_balance"] - release, 2)
    account["balance"] = round(account["balance"] + release, 2)

    account["total_trade_count"] = account.get("total_trade_count", 0) + 1
    save_account(group_id, user_id, account)
    return liab


def get_liability(account: Dict[str, Any], stock_code: str) -> Optional[Dict[str, Any]]:
    """获取做空负债，不存在返回 None。"""
    return account.get("liabilities", {}).get(stock_code)


# ========== 资产计算 ==========

def get_total_assets(account: Dict[str, Any], group_id: str) -> float:
    """
    计算总资产 = 可用余额 + 冻结保证金 + 持仓市值 − 负债 − 做空浮亏。
    """
    prices = load_prices(group_id).get("current", {})

    total = account.get("balance", 0.0)
    total += account.get("frozen_balance", 0.0)

    # 持仓市值（含杠杆部分）
    for code, pos in account.get("positions", {}).items():
        qty = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
        price = prices.get(code, 100.0)
        total += qty * price

    # 减去杠杆欠款
    for _, pos in account.get("positions", {}).items():
        total -= pos.get("debt", 0.0)

    # 做空负债浮亏（当前价 > 开仓价 = 亏损）
    for code, liab in account.get("liabilities", {}).items():
        current_price = prices.get(code, 100.0)
        short_price = liab.get("short_price", 0.0)
        short_qty = liab.get("short_quantity", 0)
        # 若当前价高于开仓价，做空浮亏
        if current_price > short_price:
            total -= (current_price - short_price) * short_qty
        # 若当前价低于开仓价，做空浮盈（加到总资产）
        else:
            total += (short_price - current_price) * short_qty

    return round(total, 2)


def get_margin_ratio(account: Dict[str, Any], group_id: str) -> float:
    """
    计算保证金率（用于杠杆爆仓判定）。
    MR = (持仓市值 − 欠款) / 持仓市值
    若无杠杆持仓，返回 1.0。
    """
    prices = load_prices(group_id).get("current", {})
    positions = account.get("positions", {})

    total_market_value = 0.0
    total_debt = 0.0

    for code, pos in positions.items():
        qty = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
        price = prices.get(code, 100.0)
        total_market_value += qty * price
        total_debt += pos.get("debt", 0.0)

    if total_market_value <= 0:
        return 1.0

    return round((total_market_value - total_debt) / total_market_value, 4)


def get_position_value(account: Dict[str, Any], stock_code: str, group_id: str) -> float:
    """计算某股票持仓的当前市值。"""
    prices = load_prices(group_id).get("current", {})
    pos = account.get("positions", {}).get(stock_code)
    if pos is None:
        return 0.0
    qty = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
    price = prices.get(stock_code, 100.0)
    return round(qty * price, 2)


def get_all_positions_value(account: Dict[str, Any], group_id: str) -> float:
    """计算所有持仓的总市值。"""
    total = 0.0
    for code in account.get("positions", {}):
        total += get_position_value(account, code, group_id)
    return round(total, 2)


# ========== 破产判定 ==========

def is_bankrupt(account: Dict[str, Any], group_id: str) -> bool:
    """判断账户是否破产（总资产 < 阈值）。"""
    config = load_group_config(group_id)
    threshold = config.get("bankruptcy_threshold", 50.0)
    return get_total_assets(account, group_id) < threshold


def apply_bankruptcy_recovery(group_id: str, user_id: str) -> str:
    """
    执行破产恢复：清除微持仓、重置现金为 200、当日禁杠杆。
    返回结果描述字符串。
    """
    config = load_group_config(group_id)
    recovery = config.get("bankruptcy_recovery", 200.0)

    account = get_or_create_account(user_id, group_id)

    if not is_bankrupt(account, group_id):
        return "❌ 你的总资产尚未跌破破产线，无法申请破产恢复。"

    if account.get("bankruptcy_used_today", False):
        return "❌ 你今天已经申请过破产恢复了，明天再来吧。"

    # 清除所有持仓和负债
    account["positions"] = {}
    account["liabilities"] = {}
    account["balance"] = recovery
    account["frozen_balance"] = 0.0
    account["bankruptcy_used_today"] = True
    # 当日禁止杠杆（到明天 00:00）
    account["no_leverage_until"] = (
        datetime.now().replace(hour=23, minute=59, second=59).isoformat()
    )

    save_account(group_id, user_id, account)

    return (
        f"🆘 破产恢复成功！\n"
        f"你的账户已重置为 {recovery} 金币。\n"
        f"⚠️ 今日内禁止使用杠杆，稳健交易重新起家吧。"
    )


# ========== 体力值管理 ==========

def check_stamina(group_id: str, user_id: str) -> Tuple[bool, int]:
    """
    检查是否有足够的交易体力。
    返回 (是否可交易, 当前体力值)。
    """
    account = get_or_create_account(user_id, group_id)
    stamina = account.get("stamina", 10)
    return stamina >= 1, stamina


def consume_stamina(group_id: str, user_id: str) -> int:
    """
    消耗 1 点体力值。返回消耗后的体力值。
    调用前应先 check_stamina。
    """
    account = get_or_create_account(user_id, group_id)
    account["stamina"] = max(0, account.get("stamina", 10) - 1)
    account["stamina_updated_at"] = datetime.now().isoformat()
    save_account(group_id, user_id, account)
    return account["stamina"]


def recover_stamina_for_all_groups(known_group_ids: list[str]) -> None:
    """全局体力恢复：所有用户 +1 体力（不超过上限）。由 scheduler 每 30 分钟调用。"""
    for gid in known_group_ids:
        config = load_group_config(gid)
        stamina_max = config.get("stamina_max", 10)
        with locked_accounts(gid) as accounts:
            for uid, account in accounts.items():
                current = account.get("stamina", stamina_max)
                if current < stamina_max:
                    account["stamina"] = min(current + 1, stamina_max)


# ========== 限仓检查 ==========

def check_position_limit(
    group_id: str, user_id: str, stock_code: str, add_quantity: int,
) -> Tuple[bool, str]:
    """
    检查单一股票持仓是否超过总发行量的 15%。
    返回 (是否超限, 描述)。
    """
    config = load_group_config(group_id)
    total_shares = config["stocks"][stock_code]["total_shares"]
    limit_ratio = config.get("position_limit_ratio", 0.15)
    limit = int(total_shares * limit_ratio)

    account = get_or_create_account(user_id, group_id)
    pos = account.get("positions", {}).get(stock_code)
    current_qty = (pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)) if pos else 0

    # 做空也计入（融券卖出 = 占用发行量）
    liab = account.get("liabilities", {}).get(stock_code)
    short_qty = liab.get("short_quantity", 0) if liab else 0

    total_held = current_qty + short_qty + add_quantity
    if total_held > limit:
        return False, (
            f"⚠️ 限仓提示：该股票总发行量 {total_shares} 股，单一用户上限 {limit} 股 "
            f"({limit_ratio*100:.0f}%)。你当前持有 {current_qty + short_qty} 股，"
            f"本次操作后将达到 {total_held} 股，超出限制。"
        )

    return True, ""


# ========== 杠杆日息扣除 ==========

def charge_leverage_interest(group_id: str) -> float:
    """
    每日 00:00 调用：对所有杠杆持仓收取日息。
    利息 = 欠款 × 日息率（默认 0.2%），利息加到 debt 上。
    返回该群本次收取的利息总额。
    """
    config = load_group_config(group_id)
    rate = config.get("leverage_interest_rate", 0.002)

    total_interest = 0.0

    with locked_accounts(group_id) as accounts:
        for uid, account in accounts.items():
            for code, pos in account.get("positions", {}).items():
                debt = pos.get("debt", 0.0)
                lev_qty = pos.get("leveraged_quantity", 0)
                if debt > 0 and lev_qty > 0:
                    interest = round(debt * rate, 2)
                    pos["debt"] = round(debt + interest, 2)
                    total_interest += interest

    return round(total_interest, 2)