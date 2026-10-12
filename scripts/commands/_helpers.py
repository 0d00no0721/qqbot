# -*- coding: utf-8 -*-
"""
共享辅助函数 — 被多个功能模块依赖的工具。
"""

try:
    from scripts.llm_pool import chat, chat_sync, available_models, LLMPoolExhausted
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import chat, chat_sync, available_models, LLMPoolExhausted

from . import _state


async def call_deepseek(user_message: str, system_prompt: str, max_tokens: int = 150) -> str:
    """调用中央模型池（完全异步）。供 Yau / 记忆分类 等共用。

    函数名沿用历史命名（原为 DeepSeek 云端直连）；出口已统一为
    scripts/llm_pool.py 的本地网关模型池，池内按优先级链自动故障转移。
    失败时抛 LLMPoolExhausted（RuntimeError 子类），调用点异常处理风格不变。
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message or "iyau"},
    ]
    text, _model = await chat(
        messages,
        label="helpers",
        max_tokens=max_tokens,
        temperature=0.8,
        timeout=float(_state.YAU_TIMEOUT),
    )
    return text
