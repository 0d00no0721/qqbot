#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · AMM 做市商 — 买卖执行、点差、手续费。

点差双轨制：
    Ask（买入价）= P × (1 + 0.005)
    Bid（卖出价）= P × (1 - 0.005)

手续费：
    买入做多  1.0%
    卖出平多  1.5%  (1.0% 手续费 + 0.5% 印花税)
    融券做空  2.0%
    买回平空  1.0%
    杠杆利息  0.2%/日（由 scheduler 每日扣除）
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from .data import load_group_config, load_prices, save_prices, STOCK_CODES
from .engine import get_price
from .account import (
    get_or_create_account,
    add_balance, deduct_balance, freeze_balance, unfreeze_balance,
    add_position, remove_position,
    add_liability, remove_liability,
    get_position, get_liability,
    get_total_assets, get_margin_ratio,
    check_stamina, consume_stamina,
    check_position_limit,
)
from .risk import (
    check_circuit_breaker, is_trading_halted,
    collect_trading_fees,
)


# ========== 手续费率 ==========

FEE_RATES = {
    "buy_long":   0.010,   # 1.0%
    "sell_long":  0.015,   # 1.5% (含印花税)
    "sell_short": 0.020,   # 2.0%
    "cover_short": 0.010,  # 1.0%
}

SPREAD = 0.005  # 点差 0.5%


# ========== TradeResult ==========

@dataclass
class TradeResult:
    success: bool
    message: str
    stock_code: str = ""
    trade_type: str = ""
    quantity: int = 0
    price: float = 0.0
    total_amount: float = 0.0
    fee: float = 0.0
    leverage: int = 1
    new_balance: float = 0.0


# ========== 价格计算 ==========

def get_ask_price(group_id: str, stock_code: str) -> float:
    """买入价（群友向机器人买入）。"""
    p = get_price(group_id, stock_code)
    return round(p * (1 + SPREAD), 4)


def get_bid_price(group_id: str, stock_code: str) -> float:
    """卖出价（群友向机器人卖出）。"""
    p = get_price(group_id, stock_code)
    return round(p * (1 - SPREAD), 4)


def calculate_fee(amount: float, trade_type: str) -> float:
    """根据交易类型计算手续费。"""
    rate = FEE_RATES.get(trade_type, 0.0)
    return round(amount * rate, 2)


# ========== 交易操作 ==========

def buy_long(
    group_id: str, user_id: str, stock_code: str,
    quantity: int, leverage: int = 1,
) -> TradeResult:
    """
    做多买入。
    leverage: 1=无杠杆, 2~3=杠杆倍数。
    """
    if quantity <= 0:
        return TradeResult(False, "❌ 买入数量必须大于 0。")

    if stock_code not in STOCK_CODES:
        return TradeResult(False, f"❌ 未知股票代码: {stock_code}")

    config = load_group_config(group_id)
    leverage_max = config.get("leverage_max", 3)
    if leverage < 1 or leverage > leverage_max:
        return TradeResult(False, f"❌ 杠杆倍数必须在 1~{leverage_max} 之间。")

    # 熔断检查
    halted, halt_msg = is_trading_halted(group_id, stock_code)
    if halted:
        return TradeResult(False, halt_msg)

    # 体力检查
    has_stamina, stamina = check_stamina(group_id, user_id)
    if not has_stamina:
        return TradeResult(False, f"❌ 交易体力不足（当前 {stamina} 点），请等待恢复。")

    # 限仓检查
    ok, limit_msg = check_position_limit(group_id, user_id, stock_code, quantity)
    if not ok:
        return TradeResult(False, limit_msg)

    # 破产后禁杠杆检查
    if leverage > 1:
        account = get_or_create_account(user_id, group_id)
        no_lev_until = account.get("no_leverage_until")
        if no_lev_until:
            from datetime import datetime
            try:
                if datetime.now() < datetime.fromisoformat(no_lev_until):
                    return TradeResult(False, "❌ 你今天申请过破产恢复，当日禁止使用杠杆。")
            except Exception:
                pass

    # 计算价格和金额
    ask = get_ask_price(group_id, stock_code)
    total_cost = ask * quantity
    fee = calculate_fee(total_cost, "buy_long")

    # 杠杆：用户只需支付本金 = 总成本 / 杠杆倍数
    if leverage > 1:
        user_pay = total_cost / leverage
        debt = total_cost - user_pay  # 借庄家的钱
    else:
        user_pay = total_cost
        debt = 0.0

    total_deduct = user_pay + fee

    # 余额检查
    ok, balance = deduct_balance(group_id, user_id, total_deduct)
    if not ok:
        return TradeResult(False, f"❌ 金币不足。需要 {total_deduct:.2f}，当前余额 {balance:.2f}。")

    # 扣体力
    consume_stamina(group_id, user_id)

    # 更新持仓
    leveraged_qty = quantity if leverage > 1 else 0
    add_position(
        group_id, user_id, stock_code,
        quantity=quantity if leverage == 1 else 0,
        price=ask,
        leveraged=leveraged_qty,
        leverage_multiplier=leverage,
        debt=debt,
    )

    # 手续费进入生态基金
    collect_trading_fees(fee)

    # 获取更新后余额
    account = get_or_create_account(user_id, group_id)

    return TradeResult(
        success=True,
        message=(
            f"✅ 买入成功！\n"
            f"股票: {stock_code}\n"
            f"数量: {quantity} 股\n"
            f"成交价: {ask:.2f}\n"
            f"总金额: {total_cost:.2f}\n"
            f"手续费: {fee:.2f}\n"
            f"{'杠杆: ' + str(leverage) + '倍 (借款 ' + f'{debt:.2f})' + chr(10) if leverage > 1 else ''}"
            f"余额: {account['balance']:.2f}"
        ),
        stock_code=stock_code,
        trade_type="buy_long",
        quantity=quantity,
        price=ask,
        total_amount=total_cost,
        fee=fee,
        leverage=leverage,
        new_balance=account["balance"],
    )


def sell_long(
    group_id: str, user_id: str, stock_code: str, quantity: int,
) -> TradeResult:
    """卖出平多。先卖杠杆部分，再卖自有部分。"""
    if quantity <= 0:
        return TradeResult(False, "❌ 卖出数量必须大于 0。")

    if stock_code not in STOCK_CODES:
        return TradeResult(False, f"❌ 未知股票代码: {stock_code}")

    # 熔断检查
    halted, halt_msg = is_trading_halted(group_id, stock_code)
    if halted:
        return TradeResult(False, halt_msg)

    # 体力检查
    has_stamina, stamina = check_stamina(group_id, user_id)
    if not has_stamina:
        return TradeResult(False, f"❌ 交易体力不足（当前 {stamina} 点），请等待恢复。")

    account = get_or_create_account(user_id, group_id)
    pos = get_position(account, stock_code)
    if pos is None:
        return TradeResult(False, f"❌ 你没有持有 {stock_code}。")

    total_held = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
    if quantity > total_held:
        return TradeResult(False, f"❌ 持仓不足。你持有 {total_held} 股，尝试卖出 {quantity} 股。")

    # 计算卖出收入
    bid = get_bid_price(group_id, stock_code)
    gross = bid * quantity
    fee = calculate_fee(gross, "sell_long")
    net = gross - fee

    # 如果卖出包含杠杆部分，需要偿还债务
    leveraged_qty = pos.get("leveraged_quantity", 0)
    sell_lev = min(quantity, leveraged_qty)
    sell_own = quantity - sell_lev

    # 计算需偿还的债务（按比例）
    debt_repay = 0.0
    if sell_lev > 0 and leveraged_qty > 0:
        debt_ratio = sell_lev / leveraged_qty
        debt_repay = pos.get("debt", 0.0) * debt_ratio

    # 实际到账 = 卖出收入 - 偿还债务
    actual_credit = net - debt_repay

    # 更新余额
    add_balance(group_id, user_id, actual_credit)

    # 扣体力
    consume_stamina(group_id, user_id)

    # 更新持仓
    remove_position(group_id, user_id, stock_code, sell_own, sell_lev)

    # 如果有杠杆债务偿还，更新持仓中的 debt
    if debt_repay > 0 and sell_lev < leveraged_qty:
        from .account import update_position_debt
        update_position_debt(group_id, user_id, stock_code, -debt_repay)

    # 手续费进入生态基金
    collect_trading_fees(fee)

    account = get_or_create_account(user_id, group_id)

    return TradeResult(
        success=True,
        message=(
            f"✅ 卖出成功！\n"
            f"股票: {stock_code}\n"
            f"数量: {quantity} 股\n"
            f"成交价: {bid:.2f}\n"
            f"总收入: {gross:.2f}\n"
            f"手续费: {fee:.2f}\n"
            f"{'偿还借款: ' + f'{debt_repay:.2f}' + chr(10) if debt_repay > 0 else ''}"
            f"到账: {actual_credit:.2f}\n"
            f"余额: {account['balance']:.2f}"
        ),
        stock_code=stock_code,
        trade_type="sell_long",
        quantity=quantity,
        price=bid,
        total_amount=gross,
        fee=fee,
        new_balance=account["balance"],
    )


def sell_short(
    group_id: str, user_id: str, stock_code: str, quantity: int,
) -> TradeResult:
    """
    融券做空：向庄家借股票卖出。
    1. 冻结 100% 保证金
    2. 庄家借出股票并以当前 Bid 卖出，收入存入冻结账户
    3. 平仓时买回归还
    """
    if quantity <= 0:
        return TradeResult(False, "❌ 做空数量必须大于 0。")

    if stock_code not in STOCK_CODES:
        return TradeResult(False, f"❌ 未知股票代码: {stock_code}")

    # 熔断检查
    halted, halt_msg = is_trading_halted(group_id, stock_code)
    if halted:
        return TradeResult(False, halt_msg)

    # 体力检查
    has_stamina, stamina = check_stamina(group_id, user_id)
    if not has_stamina:
        return TradeResult(False, f"❌ 交易体力不足（当前 {stamina} 点），请等待恢复。")

    # 限仓检查
    ok, limit_msg = check_position_limit(group_id, user_id, stock_code, quantity)
    if not ok:
        return TradeResult(False, limit_msg)

    bid = get_bid_price(group_id, stock_code)
    gross = bid * quantity
    fee = calculate_fee(gross, "sell_short")

    # 保证金 = 卖出收入（100%），加上手续费从余额扣
    margin = gross  # 冻结的保证金 = 卖出总收入
    total_deduct = fee  # 手续费从余额扣

    # 余额检查（只需付手续费）
    ok, balance = deduct_balance(group_id, user_id, total_deduct)
    if not ok:
        return TradeResult(False, f"❌ 金币不足。需要手续费 {total_deduct:.2f}，当前余额 {balance:.2f}。")

    # 冻结保证金（做空收入也冻结）
    # 实际上保证金 = 用户自有资金等额于卖出收入
    # 这里简化：冻结卖出收入作为保证金
    freeze_balance(group_id, user_id, margin)

    # 扣体力
    consume_stamina(group_id, user_id)

    # 记录做空负债
    add_liability(group_id, user_id, stock_code, quantity, bid, margin)

    # 手续费进入生态基金
    collect_trading_fees(fee)

    account = get_or_create_account(user_id, group_id)

    return TradeResult(
        success=True,
        message=(
            f"✅ 做空成功！\n"
            f"股票: {stock_code}\n"
            f"数量: {quantity} 股\n"
            f"开仓价: {bid:.2f}\n"
            f"冻结保证金: {margin:.2f}\n"
            f"手续费: {fee:.2f}\n"
            f"余额: {account['balance']:.2f}"
        ),
        stock_code=stock_code,
        trade_type="sell_short",
        quantity=quantity,
        price=bid,
        total_amount=gross,
        fee=fee,
        new_balance=account["balance"],
    )


def cover_short(
    group_id: str, user_id: str, stock_code: str, quantity: int,
) -> TradeResult:
    """
    空头平仓：以当前 Ask 买回股票归还庄家。
    盈亏 = (开仓价 - 平仓价) × 数量
    """
    if quantity <= 0:
        return TradeResult(False, "❌ 平空数量必须大于 0。")

    if stock_code not in STOCK_CODES:
        return TradeResult(False, f"❌ 未知股票代码: {stock_code}")

    # 熔断检查
    halted, halt_msg = is_trading_halted(group_id, stock_code)
    if halted:
        return TradeResult(False, halt_msg)

    # 体力检查
    has_stamina, stamina = check_stamina(group_id, user_id)
    if not has_stamina:
        return TradeResult(False, f"❌ 交易体力不足（当前 {stamina} 点），请等待恢复。")

    account = get_or_create_account(user_id, group_id)
    liab = get_liability(account, stock_code)
    if liab is None:
        return TradeResult(False, f"❌ 你没有 {stock_code} 的做空仓位。")

    short_qty = liab.get("short_quantity", 0)
    if quantity > short_qty:
        return TradeResult(False, f"❌ 做空仓位不足。当前做空 {short_qty} 股，尝试平仓 {quantity} 股。")

    # 以 Ask 价买回
    ask = get_ask_price(group_id, stock_code)
    buy_cost = ask * quantity
    fee = calculate_fee(buy_cost, "cover_short")

    short_price = liab.get("short_price", 0.0)
    margin = liab.get("frozen_margin", 0.0)

    # 计算盈亏
    # 开仓时冻结了 margin = short_price * quantity（按比例）
    margin_release = margin * quantity / short_qty if short_qty > 0 else 0
    # 买回成本 + 手续费
    total_buy = buy_cost + fee
    # 净盈亏 = 释放的保证金 - 买回成本
    net_pnl = margin_release - total_buy

    # 释放保证金
    unfreeze_balance(group_id, user_id, margin_release)

    # 如果净盈亏为正，额外加到余额；为负，从余额扣
    if net_pnl >= 0:
        add_balance(group_id, user_id, net_pnl)
    else:
        ok, balance = deduct_balance(group_id, user_id, abs(net_pnl))
        if not ok:
            # 余额不足以承担亏损，扣到归零
            add_balance(group_id, user_id, -balance)

    # 扣体力
    consume_stamina(group_id, user_id)

    # 更新做空负债
    remove_liability(group_id, user_id, stock_code, quantity)

    # 手续费进入生态基金
    collect_trading_fees(fee)

    account = get_or_create_account(user_id, group_id)

    pnl_str = f"+{net_pnl:.2f}" if net_pnl >= 0 else f"{net_pnl:.2f}"

    return TradeResult(
        success=True,
        message=(
            f"✅ 平空成功！\n"
            f"股票: {stock_code}\n"
            f"数量: {quantity} 股\n"
            f"开仓价: {short_price:.2f}\n"
            f"平仓价: {ask:.2f}\n"
            f"盈亏: {pnl_str}\n"
            f"手续费: {fee:.2f}\n"
            f"余额: {account['balance']:.2f}"
        ),
        stock_code=stock_code,
        trade_type="cover_short",
        quantity=quantity,
        price=ask,
        total_amount=buy_cost,
        fee=fee,
        new_balance=account["balance"],
    )


# ========== 杠杆利息扣除（由 scheduler 每日调用） ==========

def charge_leverage_interest(group_id: str, user_id: str) -> float:
    """
    对杠杆持仓收取每日利息 (0.2%)。
    返回扣除的利息总额。
    """
    config = load_group_config(group_id)
    rate = config.get("leverage_interest_rate", 0.002)

    account = get_or_create_account(user_id, group_id)
    total_interest = 0.0

    for code, pos in account.get("positions", {}).items():
        debt = pos.get("debt", 0.0)
        if debt > 0:
            interest = round(debt * rate, 2)
            total_interest += interest
            # 从余额扣除利息
            ok, balance = deduct_balance(group_id, user_id, interest)
            if not ok:
                # 余额不足，扣到归零，剩余计入债务
                actual = balance
                deduct_balance(group_id, user_id, actual)
                remaining = interest - actual
                from .account import update_position_debt
                update_position_debt(group_id, user_id, code, remaining)
            # 利息进入生态基金
            collect_trading_fees(min(interest, total_interest))

    return total_interest