# -*- coding: utf-8 -*-
"""与机器人版本无关的语音服务入口；禁用时不加载 Edge、不创建客户端或任务。"""

import os
from pathlib import Path

from .service import VoiceService


_service = None


def get_service():
    global _service
    if _service is None:
        path = os.getenv("QQBOT_VOICE_CONFIG") or Path(__file__).resolve().parents[2] / "config" / "voice_output.json"
        _service = VoiceService(path)
    return _service


async def disconnect(owner):
    if _service is not None:
        await _service.disconnect(owner)


async def close():
    global _service
    if _service is not None:
        await _service.close()
        _service = None
