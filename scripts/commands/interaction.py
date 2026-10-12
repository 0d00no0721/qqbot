# -*- coding: utf-8 -*-
"""互动菜单 — 复用与戳一戳相同的入口。"""

from . import _state
from ._registry import register, CommandContext


@register("互动菜单", match_type="exact")
async def cmd_interaction_menu(ctx: CommandContext) -> bool:
    menus = _state.interaction_menus
    if menus is None or not menus.enabled(ctx.group_id):
        await ctx.send("本群尚未启用互动菜单，请管理员在群功能管理中开启。", at_user=ctx.uid_str)
        return True
    task = menus.start_menu(ctx.websocket, ctx.data)
    if task is not None:
        await task
    return True
