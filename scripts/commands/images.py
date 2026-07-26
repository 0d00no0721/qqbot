# -*- coding: utf-8 -*-
"""图片功能 — 猫娘 + Pixiv 排行榜。"""

import base64

from . import _state
from ._registry import register, CommandContext
from scripts.nekosia_image import fetch_catgirl_image
from scripts.pixiv_helper import fetch_random_pixiv_image, download_pixiv_image


@register("猫娘", match_type="exact")
async def cmd_catgirl(ctx: CommandContext) -> bool:
    ctx.log(f"[猫娘] 群{ctx.group_id} 用户{ctx.user_id} 请求猫娘图片")
    image_url = await fetch_catgirl_image()
    if not image_url:
        ctx.log(f"[猫娘] 获取图片失败")
        await ctx.send("暂时获取不到猫娘图片，稍后再试试~", at_user=ctx.uid_str)
        return True
    segments = [
        {"type": "at", "data": {"qq": ctx.uid_str}},
        {"type": "image", "data": {"file": image_url}},
    ]
    await ctx.send_segments(segments)
    return True


_PIXIV_RANK_MAP = {
    "pixiv随机日榜": "daily",
    "pixiv随机周榜": "weekly",
    "pixiv随机月榜": "monthly",
}


def _pixiv_match(msg: str):
    if msg in _PIXIV_RANK_MAP:
        return _PIXIV_RANK_MAP[msg]
    return False


@register("pixiv日榜", match_type="custom", pattern=_pixiv_match)
async def cmd_pixiv(ctx: CommandContext, rank_type: str) -> bool:
    label = ctx.clean_message
    ctx.log(f"[Pixiv] 群{ctx.group_id} 用户{ctx.user_id} 请求 {label}")
    result = await fetch_random_pixiv_image(rank_type)
    if not result:
        ctx.log(f"[Pixiv] 获取 {label} 图片失败")
        await ctx.send(f"暂时获取不到{label}的图片，稍后再试试~", at_user=ctx.uid_str)
        return True
    img_bytes = await download_pixiv_image(result["image_url"])
    if not img_bytes:
        ctx.log(f"[Pixiv] 下载 {label} 图片失败")
        await ctx.send(f"暂时获取不到{label}的图片，稍后再试试~", at_user=ctx.uid_str)
        return True
    b64_data = base64.b64encode(img_bytes).decode()
    full_url = result['rank_list_url']
    segments = [
        {"type": "at", "data": {"qq": ctx.uid_str}},
        {"type": "image", "data": {"file": f"base64://{b64_data}"}},
        {"type": "text", "data": {"text": f"\n完整榜单请访问：{full_url}"}},
    ]
    ok = await ctx.send_segments(segments)
    if ok:
        ctx.log(f"[Pixiv] 已推送 {label}: {result['title']} (ID: {result['artwork_id']})")
    return True
