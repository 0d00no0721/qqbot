#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股板块 · 包入口。

对外提供：
- on_message()             — 消息钩子，每条群消息调用，采集定价指标
- handle_vs_command()      — 指令处理，由 command_handler 分发
- is_vs_command()          — 快速判断文本是否为虚拟股指令
- start_scheduler()        — 启动后台定时任务（由 reverse_bot 的 event loop 管理）
- stop_scheduler()         — 停止调度器
- register_group()         — 向调度器注册新群
- init_group_data()        — 初始化某群的虚拟股数据
- get_stock_info()         — 查询股票信息（供外部模块使用）
- get_all_stocks()         — 查询全部股票信息
- get_account()            — 查询用户账户（供签到联动等使用）
- get_total_assets()       — 计算用户总资产（供签到联动等使用）

版本：1.0.0
"""

from .data import STOCK_CODES, DEFAULT_STOCK_NAMES, load_group_config, init_group_data
from .engine import (
    on_message,
    refresh_prices,
    get_price,
    get_all_prices,
    get_price_history,
    get_stock_info,
    get_all_stocks,
)


# ========== 账户工具（轻量封装，避免循环导入） ==========

def get_account(group_id: str, user_id: str) -> dict | None:
    """查询用户虚拟股账户，不存在返回 None。"""
    from .data import load_account
    return load_account(group_id, user_id)


def get_total_assets(group_id: str, user_id: str) -> float:
    """
    计算用户虚拟股总资产 = 可用余额 + 冻结保证金 + 持仓市值 − 负债。
    供签到联动等外部模块使用。
    """
    from .data import load_account, load_prices
    account = load_account(group_id, user_id)
    if account is None:
        return 0.0

    prices = load_prices(group_id).get("current", {})

    total = account.get("balance", 0.0)
    total += account.get("frozen_balance", 0.0)

    # 持仓市值
    positions = account.get("positions", {})
    for code, pos in positions.items():
        qty = pos.get("quantity", 0) + pos.get("leveraged_quantity", 0)
        price = prices.get(code, 100.0)
        total += qty * price

    # 减去负债（杠杆欠款 + 做空亏损）
    for _, pos in positions.items():
        total -= pos.get("debt", 0.0)
    for _, liab in account.get("liabilities", {}).items():
        short_qty = liab.get("short_quantity", 0)
        short_price = liab.get("short_price", 0)
        current_price = prices.get(liab.get("stock_code", ""), 100.0)
        # 做空负债 = 若当前价 > 开仓价，有浮亏
        total -= max(0, (current_price - short_price) * short_qty)

    return total


# ========== 调度器（延迟导入避免循环依赖） ==========

def start_scheduler(broadcast):
    """启动虚拟股定时任务调度器。"""
    from .scheduler import start_scheduler as _start
    return _start(broadcast)


def stop_scheduler():
    """停止虚拟股定时任务调度器。"""
    from .scheduler import stop_scheduler as _stop
    _stop()


def register_group(group_id: str):
    """向调度器注册新群。"""
    from .scheduler import register_group as _reg
    _reg(group_id)


# ========== 指令处理（延迟导入） ==========

def handle_vs_command(group_id: str, user_id: str, text: str):
    """处理虚拟股指令，返回回复文本或 None。"""
    from .commands import handle_vs_command as _handle
    return _handle(group_id, user_id, text)


def is_vs_command(text: str) -> bool:
    """快速判断文本是否以虚拟股指令开头。"""
    from .commands import is_vs_command as _is
    return _is(text)


__all__ = [
    # engine
    "on_message",
    "refresh_prices",
    "get_price",
    "get_all_prices",
    "get_price_history",
    "get_stock_info",
    "get_all_stocks",
    # data
    "STOCK_CODES",
    "DEFAULT_STOCK_NAMES",
    "load_group_config",
    "init_group_data",
    # account helpers
    "get_account",
    "get_total_assets",
    # scheduler
    "start_scheduler",
    "stop_scheduler",
    "register_group",
    # commands
    "handle_vs_command",
    "is_vs_command",
]