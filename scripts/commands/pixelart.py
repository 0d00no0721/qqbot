# -*- coding: utf-8 -*-
"""像素画 — 图片转 32×32 像素 + LaTeX 表格文件。"""

import os

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

from . import _state
from ._registry import register, CommandContext


async def download_image(url: str, save_path: str):
    """异步下载图片到本地文件"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(await resp.read())


def pixelate_and_create_tex(image_path: str, preview_path: str, tex_path: str):
    """将图片像素化并生成预览图和 LaTeX 表格文件"""
    img = Image.open(image_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    small_img = img.resize((_state.PIXEL_SIZE, _state.PIXEL_SIZE), Image.NEAREST)

    preview_img = small_img.resize(
        (_state.PIXEL_SIZE * _state.PREVIEW_SCALE, _state.PIXEL_SIZE * _state.PREVIEW_SCALE),
        Image.NEAREST,
    )
    preview_img.save(preview_path)

    lines = [
        "\\documentclass{article}",
        "\\usepackage[table]{xcolor}",
        "\\begin{document}",
        "\\resizebox{\\linewidth}{!}{%",
        "\\begin{tabular}{" + "*{" + str(_state.PIXEL_SIZE) + "}{c}" + "}",
    ]
    for y in range(_state.PIXEL_SIZE):
        row = []
        for x in range(_state.PIXEL_SIZE):
            r, g, b = small_img.getpixel((x, y))
            row.append(
                f"\\cellcolor[rgb]{{{r/255:.2f},{g/255:.2f},{b/255:.2f}}}\\rule{{0.3em}}{{0.3em}}"
            )
        lines.append(" & ".join(row) + " \\\\")
    lines.extend(["\\end{tabular}%", "}", "\\end{document}"])

    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


@register("像素画", match_type="exact")
async def cmd_pixelart(ctx: CommandContext) -> bool:
    if not HAS_PIL or not HAS_AIOHTTP:
        await ctx.send("像素画功能缺少依赖，请联系管理员安装 Pillow 和 aiohttp", at_user=ctx.uid_str)
        return True
    image_url = None
    msg_segments = ctx.data.get("message", [])
    if isinstance(msg_segments, list):
        for seg in msg_segments:
            if isinstance(seg, dict) and seg.get("type") == "image":
                image_url = seg.get("data", {}).get("url")
                break
    if not image_url:
        await ctx.send('请发送一张图片并用"像素画"指令', at_user=ctx.uid_str)
        return True
    try:
        await ctx.send("正在处理像素画，请稍候~", at_user=ctx.uid_str)
        await download_image(image_url, _state.TEMP_IMAGE_PATH)
        pixelate_and_create_tex(_state.TEMP_IMAGE_PATH, _state.TEMP_PREVIEW_PATH, _state.TEMP_TEX_PATH)
        await ctx.send_image(_state.TEMP_PREVIEW_PATH)
        await ctx.send_file(_state.TEMP_TEX_PATH)
    except Exception as e:
        ctx.log(f"[像素画] 处理失败: {e}")
        await ctx.send("像素画生成失败，请检查图片格式或稍后再试", at_user=ctx.uid_str)
    finally:
        for p in [_state.TEMP_IMAGE_PATH, _state.TEMP_PREVIEW_PATH, _state.TEMP_TEX_PATH]:
            if os.path.exists(p):
                os.remove(p)
    return True
