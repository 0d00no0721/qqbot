#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 风控系统 — 爆仓、熔断、体力值、限仓。

所有风控逻辑在交易执行前生效，是交易路径上的"四道门"：
    1. 熔断检查     → 该股/全盘是否停牌？
    2. 体力值检查   → 体力是否 ≥ 1？
    3. 限仓检查     → 买入后是否超 15%？
    4. 余额/保证金  → 在 account/market 中执行
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .data import (
    STOCK_CODES, load_group_config, load_prices, save_prices,
    load_accounts, save_accounts, locked_accounts,
    load_ecosystem_fund, save_ecosystem_fund,
)
from .account import get_or_create_account, get_margin_ratio, get_total_assets
from .engine import get_all_prices, get_price_history


# ========== 数据结构 ==========

@dataclass
class LiquidationEvent:
    """爆仓事件。"""
    user_id: str
    group_id: str
    stock_code: str
    margin_ratio: float
    market_value: float
    debt: float
    recovered: float          # 扣除欠款后退还给用户的残余
    message: str              # 广播消息


@dataclass
class CircuitBreakerStatus:
    """熔断状态。"""
    is_halted: bool
    reason: str               # "single_30%" / "market_15%" / ""
    halt_until: Optional[str]  # ISO 时间戳
    stock_code: Optional[str]  # 触发熔断的股票（大盘熔断时为 None）


# ========== 熔断 ==========

def check_circuit_breaker(group_id: str, stock_code: Optional[str] = None) -> CircuitBreakerStatus:
    """
    检查熔断状态。可检查单股或全盘。

    参数：
        group_id: 群号
        stock_code: 指定股票代码。None 表示检查全盘。
    """
    prices = load_prices(group_id)
    config = load_group_config(group_id)
    cb_data = prices.get("circuit_breaker", {})
    now = datetime.now()

    # ── 检查全盘熔断 ──
    for code in STOCK_CODES:
        entry = cb_data.get(code)
        if entry and entry.get("scope") == "market":
            halt_until = _parse_ts(entry.get("halt_until", ""))
            if halt_until and now < halt_until:
                return CircuitBreakerStatus(
                    is_halted=True,
                    reason=entry.get("reason", "大盘熔断"),
                    halt_until=entry["halt_until"],
                    stock_code=None,
                )
            elif halt_until and now >= halt_until:
                # 熔断到期，清除
                cb_data[code] = None

    # ── 检查单股熔断 ──
    if stock_code:
        entry = cb_data.get(stock_code)
        if entry:
            halt_until = _parse_ts(entry.get("halt_until", ""))
            if halt_until and now < halt_until:
                return CircuitBreakerStatus(
                    is_halted=True,
                    reason=entry.get("reason", "单股熔断"),
                    halt_until=entry["halt_until"],
                    stock_code=stock_code,
                )
            elif halt_until and now >= halt_until:
                cb_data[stock_code] = None

    # ── 检查是否需要新触发熔断 ──
    threshold_single = config.get("circuit_breaker_single", 0.30)
    threshold_market = config.get("circuit_breaker_market", 0.15)
    cb_hours = config.get("circuit_breaker_hours", 1)

    if stock_code:
        # 检查单股 1 小时内涨跌幅
        change = _compute_1h_change(group_id, stock_code)
        if abs(change) >= threshold_single:
            halt_until = (now + timedelta(hours=cb_hours)).isoformat()
            cb_data[stock_code] = {
                "halt_until": halt_until,
                "reason": f"1h{'涨幅' if change > 0 else '跌幅'}{abs(change)*100:.1f}%",
                "scope": "single",
            }
            prices["circuit_breaker"] = cb_data
            save_prices(group_id, prices)
            return CircuitBreakerStatus(
                is_halted=True,
                reason=cb_data[stock_code]["reason"],
                halt_until=halt_until,
                stock_code=stock_code,
            )

    # 检查大盘熔断（所有股票均价跌幅）
    market_change = _compute_market_change(group_id)
    if abs(market_change) >= threshold_market and market_change < 0:
        halt_until = (now + timedelta(hours=cb_hours)).isoformat()
        for code in STOCK_CODES:
            cb_data[code] = {
                "halt_until": halt_until,
                "reason": f"大盘跌幅{abs(market_change)*100:.1f}%",
                "scope": "market",
            }
        prices["circuit_breaker"] = cb_data
        save_prices(group_id, prices)
        return CircuitBreakerStatus(
            is_halted=True,
            reason=f"大盘跌幅{abs(market_change)*100:.1f}%",
            halt_until=halt_until,
            stock_code=None,
        )

    # 清理过期的熔断标记
    prices["circuit_breaker"] = cb_data
    save_prices(group_id, prices)

    return CircuitBreakerStatus(
        is_halted=False, reason="", halt_until=None, stock_code=stock_code
    )


def is_trading_halted(group_id: str, stock_code: str) -> Tuple[bool, str]:
    """
    快速判断某股票是否停牌。
    返回 (是否停牌, 原因消息)。
    """
    status = check_circuit_breaker(group_id, stock_code)
    if status.is_halted:
        reason = status.reason or "该股票已停牌"
        return True, f"⛔ {reason}，{stock_code} 已停牌，暂时无法交易。"
    # 也检查全盘熔断
    status_all = check_circuit_breaker(group_id, None)
    if status_all.is_halted:
        reason = status_all.reason or "大盘熔断"
        return True, f"⛔ {reason}，全盘停牌，暂时无法交易。"
    return False, ""


def get_halt_until(group_id: str, stock_code: str) -> Optional[datetime]:
    """获取停牌截止时间，未停牌返回 None。"""
    status = check_circuit_breaker(group_id, stock_code)
    if status.is_halted and status.halt_until:
        return _parse_ts(status.halt_until)
    status_all = check_circuit_breaker(group_id, None)
    if status_all.is_halted and status_all.halt_until:
        return _parse_ts(status_all.halt_until)
    return None


def _compute_1h_change(group_id: str, stock_code: str) -> float:
    """计算某股票 1 小时内的涨跌幅。"""
    history = get_price_history(group_id, stock_code, hours=1)
    if len(history) < 2:
        return 0.0
    old_price = float(history[0]["price"])
    new_price = float(history[-1]["price"])
    if old_price <= 0:
        return 0.0
    return (new_price - old_price) / old_price


def _compute_market_change(group_id: str) -> float:
    """计算大盘 1 小时内的均价变化。"""
    total_change = 0.0
    count = 0
    for code in STOCK_CODES:
        change = _compute_1h_change(group_id, code)
        total_change += change
        count += 1
    return total_change / count if count > 0 else 0.0


# ========== 爆仓检查 ==========

def check_liquidation(group_id: str) -> List[LiquidationEvent]:
    """
    遍历所有杠杆账户，检查保证金率是否 ≤ 10%。
    触发强平：卖出全部持仓 → 偿还欠款 → 退还残余。
    返回所有触发的爆仓事件列表（供 scheduler 广播）。
    """
    config = load_group_config(group_id)
    liquidation_threshold = 0.10  # MR ≤ 10%
    interest_rate = config.get("leverage_interest_rate", 0.002)

    accounts = load_accounts(group_id)
    prices = load_prices(group_id).get("current", {})
    events: List[LiquidationEvent] = []

    for user_id, account in accounts.items():
        positions = account.get("positions", {})
        for code, pos in list(positions.items()):
            lev_qty = pos.get("leveraged_quantity", 0)
            if lev_qty <= 0:
                continue

            # 计算保证金率
            mr = get_margin_ratio(account, group_id)
            if mr > liquidation_threshold:
                continue

            # 触发强平
            market_value = (pos.get("quantity", 0) + lev_qty) * prices.get(code, 100.0)
            debt = pos.get("debt", 0.0)
            # 利息（简化：按 debt 的日息计算累计）
            interest = round(debt * interest_rate, 2)
            total_debt = debt + interest

            # 卖出回收
            recovered = round(market_value - total_debt, 2)
            if recovered < 0:
                # 不够还债，庄家兜底
                recovered = 0.0

            # 更新账户：清除该持仓
            del positions[code]

            # 退还残余
            if recovered > 0:
                account["balance"] = round(account.get("balance", 0.0) + recovered, 2)

            # 构造广播消息
            loss_amount = round(debt - max(0, market_value - total_debt), 2)
            msg = (
                f"🚨 **【强平公告】** 散户 @{user_id} 的多头仓位由于"
                f"「{code}」暴跌，保证金率已跌至 {mr*100:.0f}%，"
                f"触发强制平仓！庄家已没收其持仓，"
                f"本次爆仓净亏损 **{max(0, loss_amount)} 金币**！"
            )

            events.append(LiquidationEvent(
                user_id=user_id,
                group_id=group_id,
                stock_code=code,
                margin_ratio=mr,
                market_value=market_value,
                debt=total_debt,
                recovered=recovered,
                message=msg,
            ))

    if events:
        save_accounts(group_id, accounts)

    return events


# ========== 手续费回收 ==========

def collect_trading_fee(amount: float) -> None:
    """将手续费收入生态发展基金池。"""
    fund = load_ecosystem_fund()
    save_ecosystem_fund(fund + amount)


# 别名：market.py 使用复数形式
collect_trading_fees = collect_trading_fee


# ========== 工具函数 ==========

def _parse_ts(ts: str) -> Optional[datetime]:
    """解析 ISO 时间戳，失败返回 None。"""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None