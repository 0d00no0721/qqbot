# -*- coding: utf-8 -*-
"""Yau — 丘成桐风格 AI 对话（每小时限 3 次）。"""

import os
import time
import urllib.error

import httpx

from . import _state
from ._helpers import call_deepseek
from ._registry import register, CommandContext


YAU_SYSTEM_PROMPT_BUILTIN = """你是当代著名数学家、菲尔兹奖得主、清华大学求真书院院长丘成桐。

语言风格：白话夹杂文言文，半文半白，有长辈训话的威严感。直言不讳批评学术乱象和不思进取的学生。模仿微信发言，偶尔有形近字错误、繁简混合、标点混用。整体简短有力不超过300字，禁止markdown格式。

高频词汇（可选用）：令人汗颜、居心叵测、朋比为奸、何其斤斤计较、寻天人乐处、拓万古心胸、愧对国家百姓、下等论文、无耻、以正风纪、功比孔明、忠过武穆、动人心者竟如是耶、方为上人、何其壮哉。

核心要求：语态直率威严，引经据典融合诗词歌赋和典籍史料。提升表达多样性，避免频繁使用固定句式。降低"羞也不羞"、"使人汗颜"的出现频次。论述聚焦，严禁发散。

当用户只发Yau没有具体问题时（类似iyau触发），主动从国家历史与民族命运出发，落脚到勉励学生学习，或批评近期现象。"""


def load_yau_system_prompt() -> str:
    """尝试从外部文件加载风格指令，失败则使用内置精简版"""
    style_file = os.path.join(_state.SCRIPT_DIR, "yau_style.txt")
    if os.path.exists(style_file):
        try:
            with open(style_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                if _state.bot_logger:
                    _state.bot_logger.info(f"[Yau] 从 {style_file} 加载风格指令（{len(content)} 字符）")
                return content
        except Exception as e:
            if _state.bot_logger:
                _state.bot_logger.info(f"[Yau] 读取风格文件失败: {e}")
    if _state.bot_logger:
        _state.bot_logger.info("[Yau] 使用内置精简版风格指令")
    return YAU_SYSTEM_PROMPT_BUILTIN


# 模块启动时加载 Yau 风格指令
yau_system_prompt = load_yau_system_prompt()


def check_yau_rate_limit(user_id: str) -> bool:
    """检查用户是否超过频率限制，成功则记录本次使用（需外部持 _yau_lock）"""
    now = time.time()
    usage = _state.yau_usage[user_id]
    _state.yau_usage[user_id] = [t for t in usage if now - t < _state.YAU_WINDOW]
    if len(_state.yau_usage[user_id]) >= _state.YAU_MAX_USAGE:
        return False
    _state.yau_usage[user_id].append(now)
    return True


@register("Yau", match_type="prefix")
async def cmd_yau(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[3:].strip()
    async with _state._yau_lock:
        if not check_yau_rate_limit(ctx.uid_str):
            await ctx.send("您已达到每小时使用次数上限（3次），请稍后再试。", at_user=ctx.uid_str)
            return True
    try:
        prompt = rest if rest else "iyau"
        reply = await call_deepseek(prompt, yau_system_prompt, max_tokens=_state.YAU_MAX_TOKENS)
    except (urllib.error.HTTPError, httpx.HTTPStatusError) as e:
        code = getattr(e, 'code', e.response.status_code if hasattr(e, 'response') else '?')
        ctx.log(f"[Yau] API HTTP 错误: {code}")
        reply = "AI 暂时罢工了，可能是接口超时或配额不足，请稍后重试。"
    except Exception as e:
        ctx.log(f"[Yau] API 调用失败: {e}")
        reply = "AI 暂时罢工了，请稍后重试。"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True
