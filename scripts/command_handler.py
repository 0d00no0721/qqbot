#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令处理器 — 瘦门面。

v2.4.0 重构：指令逻辑已拆分到 ``scripts/commands/`` 包（注册表模式）。
本文件仅保留：
  - 公共 API re-export（保持 ``reverse_bot.py`` 零改动）
  - ``handle_command`` 入口：虚拟股前置检查 + 构建 CommandContext + 委托 dispatch
  - ``init_handlers`` re-export

指令匹配规则、各功能实现分别位于 ``commands/`` 下的独立模块，
每个模块用 ``@register`` 装饰器声明指令，由 ``commands/__init__.py`` 统一导入触发注册。
"""

# ========== 功能开关：虚拟股市 ==========
VS_ENABLED = False  # 设为 True 启用虚拟股市板块

if VS_ENABLED:
    from scripts.virtual_stock import (
        handle_vs_command as _vs_handle,
        is_vs_command as _vs_is_command,
    )

# ========== commands 包（导入即注册所有指令） ==========
from scripts.commands import _state, _registry
from scripts.commands import *  # noqa: F401,F403  触发各子模块 @register
from scripts.commands.voice import process_voice_message

# ========== 公共 API re-export（reverse_bot.py 依赖，勿删） ==========
from scripts.commands._state import (
    init_handlers,
    BOT_QQ,
    VERSION,
    _dedup_lock,
    _mystery_queue,
    processed_msg_ids,
    PROCESSED_MSG_MAX,
    known_groups,
)

# 神秘数字处理函数（reverse_bot.py 后台任务消费）
from scripts.mystery_number import find_valid_number_async as find_valid_number


async def handle_command(websocket, data: dict, clean_message: str, raw_message: str):
    """处理 @bot 指令。返回 True 表示已处理（指令匹配），False 表示非指令。"""
    group_id = data["group_id"]
    user_id = data["user_id"]
    uid_str = str(user_id)
    sender = data.get("sender", {})
    nickname = sender.get("card") or sender.get("nickname") or "用户"

    # ===== 虚拟股指令（最高优先级，前置拦截） =====
    if VS_ENABLED and _vs_is_command(clean_message):
        reply = _vs_handle(str(group_id), uid_str, clean_message)
        if reply:
            await _state.send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 构建上下文，委托注册表分发 =====
    ctx = _registry.CommandContext(
        websocket=websocket,
        data=data,
        clean_message=clean_message,
        raw_message=raw_message,
        group_id=group_id,
        user_id=user_id,
        uid_str=uid_str,
        nickname=nickname,
    )
    return await _registry.dispatch(ctx)
