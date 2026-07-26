# -*- coding: utf-8 -*-
"""语音消息转文字 — 调用 NapCat HTTP API。"""

import re
import urllib.parse
from typing import Optional

import httpx

from . import _state


_RECORD_CQ_RE = re.compile(r'\[CQ:record,file=([^\]]*)\]')


async def _transcribe_voice(file_param: str) -> Optional[str]:
    """调用 NapCat HTTP API 将语音转写为文字。成功返回文本，失败返回 None。"""
    try:
        file_id = urllib.parse.unquote(file_param)
        async with httpx.AsyncClient(timeout=_state.VOICE_TRANSCRIBE_TIMEOUT) as client:
            resp = await client.post(
                f"{_state.NAPCAT_HTTP_URL}/fetch_ptt_text",
                json={"file_id": file_id},
                headers={"Content-Type": "application/json"},
            )
            result = resp.json()
            text = result.get("data", {}).get("text", "")
            return text.strip() if text else None
    except httpx.TimeoutException:
        if _state.bot_logger:
            _state.bot_logger.info(f"[语音转写] API 超时: file={file_param[:40]}")
    except Exception as e:
        if _state.bot_logger:
            _state.bot_logger.info(f"[语音转写] API 调用失败: {e} (file={file_param[:40]})")
    return None


async def process_voice_message(raw_message: str) -> str:
    """将消息中的 [CQ:record,file=...] 替换为转写文字。"""
    matches = list(_RECORD_CQ_RE.finditer(raw_message))
    if not matches:
        return raw_message

    text = raw_message
    for m in reversed(matches):
        file_param = m.group(1)
        if _state.bot_logger:
            _state.bot_logger.info(f"[语音转写] 收到语音消息，file={file_param[:60]}")

        result = await _transcribe_voice(file_param)
        if result:
            if _state.bot_logger:
                _state.bot_logger.info(f"[语音转写] 转写成功: {result[:60]}")
            text = text[:m.start()] + result + text[m.end():]
        else:
            if _state.bot_logger:
                _state.bot_logger.info(f"[语音转写] 转写失败，保留原 CQ 码")

    return text
