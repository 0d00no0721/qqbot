#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备用模型回复生成脚本 — DeepSeek 官方 API（付费，稳定）
=====================================================
独立进程，不与其他脚本共享任何网络状态。
通过 stdin 接收 JSON 输入，通过 stdout 输出 JSON 结果。

输入格式（JSON，通过 stdin）:
    {"context": "...", "persona": "...", "max_tokens": 500}

输出格式（JSON，通过 stdout）:
    {"reply": "..."}          成功
    {"reply": ""}             失败
"""

import asyncio
import httpx
import json
import re
import sys
import traceback

# ---------- 配置（从 .env 读取默认值） ----------
import os
API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
API_BASE_URL = "https://api.deepseek.com/v1/chat/completions"
API_MODEL = "deepseek-chat"
TIMEOUT = 30.0

# ---------- 清理 LLM 可能输出的昵称前缀 ----------
_NICKNAME_PREFIX = re.compile(r'^\[.+?\][:：]\s*')


async def generate_reply(context: str, persona: str, max_tokens: int = 500) -> str:
    """调用 DeepSeek 官方 API 生成回复。"""
    prompt = (
        f"以下是最近的群聊记录，请根据上下文自然地接话回复：\n\n{context}\n\n"
        "注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、"
        "不要使用 [昵称]: 的格式、不要包含 CQ 码。直接输出回复文本即可。"
    )
    payload = {
        "model": API_MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.85,
        "messages": [
            {"role": "system", "content": persona},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(API_BASE_URL, json=payload, headers=headers)
            if resp.status_code != 200:
                import sys; print(f"[model_fallback] API返回非200: {resp.status_code} {resp.text}", file=sys.stderr, flush=True)
                return ""
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            reply = (content or "").strip()
            reply = _NICKNAME_PREFIX.sub("", reply).strip()
            return reply
    except (httpx.TimeoutException, Exception) as e:
        import sys; print(f"[model_fallback] API调用失败: {e} | url={API_BASE_URL} model={API_MODEL}", file=sys.stderr, flush=True)
        return ""


def main():
    # 强制 stdout 输出 UTF-8（Windows 终端默认 cp950 会导致中文输出崩溃）
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    try:
        # 从 stdin 读取 JSON 输入
        raw = sys.stdin.buffer.read().decode("utf-8").strip()
        if not raw:
            sys.stdout.buffer.write(
                (json.dumps({"reply": ""}) + "\n").encode("utf-8")
            )
            return

        input_data = json.loads(raw)
        context = input_data.get("context", "（暂无上下文）")
        persona = input_data.get("persona", "")
        max_tokens = input_data.get("max_tokens", 500)

        # 支持从 stdin 覆盖模型配置（与 model_primary.py 保持一致）
        global API_MODEL, API_BASE_URL, API_KEY
        if "model" in input_data:
            API_MODEL = input_data["model"]
        if "api_base_url" in input_data:
            API_BASE_URL = input_data["api_base_url"]
        if "api_key" in input_data:
            API_KEY = input_data["api_key"]

        reply = asyncio.run(generate_reply(context, persona, max_tokens))
        # 输出结果到 stdout（使用 buffer 避免编码问题）
        sys.stdout.buffer.write(
            (json.dumps({"reply": reply}, ensure_ascii=False) + "\n").encode("utf-8")
        )

    except json.JSONDecodeError:
        sys.stdout.buffer.write(
            (json.dumps({"reply": ""}) + "\n").encode("utf-8")
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stdout.buffer.write(
            (json.dumps({"reply": ""}) + "\n").encode("utf-8")
        )


if __name__ == "__main__":
    main()