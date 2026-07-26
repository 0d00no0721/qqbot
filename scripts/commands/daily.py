# -*- coding: utf-8 -*-
"""日常功能 — 签到 + 神秘数字。"""

from . import _state
from ._registry import register, CommandContext
from scripts.sign_in import process_sign_in


@register("签到", match_type="exact")
async def cmd_sign_in(ctx: CommandContext) -> bool:
    async with _state._sign_in_lock:
        result = process_sign_in(str(ctx.group_id), ctx.uid_str)
        if result is None:
            await ctx.send("今天你已经签到过了哦~", at_user=ctx.uid_str)
            return True
        today_order = result["today_order"]
        total_days = result["total_days"]
        streak = result["streak"]
        rank = result["rank"]
        reply = (
            f"签到成功！你是今日第{today_order}位签到者，"
            f"累计签到{total_days}天，连续签到{streak}天，"
            f"全群排名第{rank}位。")
        await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("神秘数字", match_type="exact")
async def cmd_mystery_number(ctx: CommandContext) -> bool:
    await _state._mystery_queue.put((ctx.websocket, ctx.group_id, ctx.uid_str))
    await ctx.send("神秘数字召唤中，请稍候...", at_user=ctx.uid_str)
    return True
