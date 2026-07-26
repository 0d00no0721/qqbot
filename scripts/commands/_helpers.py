# -*- coding: utf-8 -*-
"""
共享辅助函数 — 被多个功能模块依赖的工具。
"""

import httpx

from . import _state


async def call_deepseek(user_message: str, system_prompt: str, max_tokens: int = 150) -> str:
    """调用 DeepSeek API（完全异步）。供 Yau / 记忆分类 等共用。"""
    timeout = httpx.Timeout(_state.YAU_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": _state.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message or "iyau"},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.8,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_state.DEEPSEEK_API_KEY}",
        }
        resp = await client.post(
            f"{_state.DEEPSEEK_BASE_URL}/chat/completions",
            json=payload, headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]
