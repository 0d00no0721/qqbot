# -*- coding: utf-8 -*-
"""显式朗读命令；仍由原有 @bot + commands 群开关控制。"""

from scripts.voice_output import get_service
from ._registry import CommandContext, register


@register("朗读", match_type="prefix")
async def cmd_read(ctx: CommandContext) -> bool:
    text = ctx.clean_message[len("朗读"):].strip()
    if not text:
        await ctx.send("用法：@bot 朗读 你好呀", at_user=ctx.uid_str)
        return True
    async def send_text(message):
        return await ctx.send(message, at_user=ctx.uid_str)
    await get_service().read(ctx.websocket, ctx.group_id, ctx.user_id,
                             ctx.data.get("message_id"), text, send_text)
    return True
