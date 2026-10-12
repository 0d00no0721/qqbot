#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备用模型回复生成脚本 — 本地网关模型池（scripts/llm_pool.py）
============================================================
独立进程，不与其他脚本共享任何网络状态。
通过 stdin 接收 JSON 输入，通过 stdout 输出 JSON 结果。

与 model_primary.py 结构一致，区别只在 llm_pool 的审计 label（便于区分
主/备两次尝试）。两者内部都走同一个本地网关模型池，各自独立按优先级链降档。

输入格式（JSON，通过 stdin）:
    {"context": "...", "persona": "...", "max_tokens": 500,
     "model": "...",            # 可选（旧字段）：优先尝试的模型
     "preferred_model": "...",  # 可选（新字段，优先于 model）：优先尝试的模型
     "api_base_url": "...",     # 可选：覆盖模型池端点（LLM_POOL_BASE_URL）
     "api_key": "...",          # 可选：覆盖模型池密钥（LLM_POOL_API_KEY）
     "target": {...},           # 可选：本轮**要回应的那条消息**
                                #   {"nickname": str, "uid": str, "content": str} 或 null
                                #   非空时改用「定向回复」prompt；null/缺失时逐字沿用旧 prompt
     "avoid_recent": ["..."]}   # 可选：本群最近 3 条自己发过的回复（防复读提示；
                                #   仅在 target 非空时生效）

输出格式（JSON，通过 stdout）:
    {"reply": "..."}          成功
    {"reply": ""}             失败
"""

import asyncio
import json
import os
import re
import sys
import traceback

# ---------- 中央模型池（本地网关，唯一模型出口） ----------
# 双路兼容导入：本文件被当脚本直接执行（sys.path[0]=scripts/）时走第二条分支。
try:
    from scripts.llm_pool import chat, LLMPoolExhausted
except ImportError:  # 以脚本方式直接运行时（sys.path[0]=scripts/）
    from llm_pool import chat, LLMPoolExhausted

# ---------- 配置（可选覆盖值，默认留空 = 直接用模型池配置） ----------
# 这些是默认值，可以从 stdin 的 model/api_base_url/api_key 字段覆盖
API_KEY = ""
API_BASE_URL = ""
API_MODEL = ""
TIMEOUT = 30.0  # 单次尝试预算上限（池内部另有 20s/次、45s 总预算约束）

# ---------- 清理 LLM 可能输出的昵称前缀 ----------
_NICKNAME_PREFIX = re.compile(r'^\[.+?\][:：]\s*')


def _apply_pool_overrides() -> None:
    """把 stdin 传入的 api_base_url/api_key 映射为模型池配置来源。

    模型池的 base_url/api_key 取自环境变量 LLM_POOL_BASE_URL / LLM_POOL_API_KEY
    （config/model_pool.json 里没有直写这两项），本进程是单次运行、首次调用前
    设置，因此覆盖对本进程生效，且不会影响父进程。留空则沿用池配置。
    """
    if API_BASE_URL.strip():
        os.environ["LLM_POOL_BASE_URL"] = API_BASE_URL.strip()
    if API_KEY.strip():
        os.environ["LLM_POOL_API_KEY"] = API_KEY.strip()


def _target_content(target) -> str:
    """取出 target 里用于判定路由的内容（已 strip）。

    与 _build_prompt 走同一套归一化，避免"日志说走新 prompt、实际走了旧 prompt"。
    """
    if not isinstance(target, dict):
        return ""
    return str(target.get("content") or "").strip()


def _injected_recent(avoid_recent) -> list:
    """取出真正会被注入 prompt 的「最近说过的话」（已 strip、去空）。

    子进程是独立契约边界：avoid_recent 里若混入 None，`str(None)` 会得到
    真值字符串 "None" 并被注入成字面量 `- None`。这里显式跳过 None，
    数字等其它类型仍按 str() 透传。
    """
    return [str(r).strip() for r in (avoid_recent or [])
            if r is not None and str(r).strip()]


def _build_prompt(context: str, target: dict = None,
                  avoid_recent: list = None) -> str:
    """拼装 user prompt（两路）。

    无 target（或 target 内容为空）：**逐字**沿用改动前的旧 prompt——
    这是向后兼容基线，也是回放对比里「修前」一侧的行为。
    有 target：明确标出「你需要回应的最新消息」，并说明背景仅用于理解语境，
    让模型针对这条作答，而不是在整段群聊记录里自由挑话头。

    本函数与 model_primary.py 中的同名函数保持逐字一致，仅在 label 上区分主/备。
    """
    target_content = _target_content(target)

    # ---------- 路 A：无 target —— 与改动前逐字相同 ----------
    if not target_content:
        return (
            f"以下是最近的群聊记录，请根据上下文自然地接话回复：\n\n{context}\n\n"
            "注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、"
            "不要使用 [昵称]: 的格式、不要包含 CQ 码。直接输出回复文本即可。"
        )

    # ---------- 路 B：有 target —— 定向回复 ----------
    nickname = str(target.get("nickname") or "").strip()
    prompt = (
        f"【你需要回应的最新消息】{nickname}: {target_content}\n\n"
        f"---- 以下为群聊背景（仅供理解语境；不要去回答背景里其它未被回应的问题）----\n"
        f"{context}\n\n"
        "注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、"
        "不要使用 [昵称]: 的格式、不要包含 CQ 码。\n"
        "请针对上面那条【你需要回应的最新消息】作答；"
        "若它在问具体事实而你不确定，就简短诚实地说明（不要编造）。\n"
        "补充：若这条消息问的是实时/事实类信息（天气、新闻、比分、股价等），"
        "不要编造具体数值或细节（气温、涨跌幅、比分、日期都不要写具体数字）"
        "——可以说「本鱼查不到实时的」，然后自然地聊两句（比如「反正带伞总没错」）。"
    )

    # ---------- 防复读：注入「你最近在本群说过的话」 ----------
    recent = _injected_recent(avoid_recent)
    if recent:
        bullets = "\n".join(f"- {r}" for r in recent)
        prompt += (
            f"\n\n你最近在本群说过：\n{bullets}\n"
            "不要重复这些内容（换角度或换说法）。"
        )

    return prompt


async def generate_reply(context: str, persona: str, max_tokens: int = 500,
                         preferred: str = "", target: dict = None,
                         avoid_recent: list = None) -> str:
    """调用本地网关模型池生成回复（池内自动按优先级链故障转移）。

    prompt 分两路（与 model_primary.py 完全一致）：
      无 target → 逐字沿用改动前的旧 prompt；
      有 target → 「你需要回应的最新消息」定向回复 prompt（+ 可选防复读提示）。
    """
    prompt = _build_prompt(context, target, avoid_recent)
    messages = [
        {"role": "system", "content": persona},
        {"role": "user", "content": prompt},
    ]

    try:
        content, model_used = await chat(
            messages,
            label="model_fallback",
            max_tokens=max_tokens,
            temperature=0.85,
            preferred=preferred or None,
            timeout=TIMEOUT,
        )
        reply = (content or "").strip()
        reply = _NICKNAME_PREFIX.sub("", reply).strip()
        print(f"[model_fallback] 调用成功: 命中模型={model_used}",
              file=sys.stderr, flush=True)
        return reply
    except LLMPoolExhausted as e:
        print(f"[model_fallback] 模型池耗尽: {e}", file=sys.stderr, flush=True)
        return ""
    except Exception as e:
        print(f"[model_fallback] 调用失败: {type(e).__name__}: {e} "
              f"| preferred={preferred or '(池默认)'}", file=sys.stderr, flush=True)
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

        # 定向回复：本轮要回应的那条消息（dict 或 null/缺失）
        target = input_data.get("target")
        if not isinstance(target, dict):
            target = None
        # 防复读：本群最近 3 条自己发过的回复
        avoid_recent = input_data.get("avoid_recent")
        if not isinstance(avoid_recent, list):
            avoid_recent = []

        # 支持从 stdin 覆盖模型配置（与 model_primary.py 保持一致）
        global API_MODEL, API_BASE_URL, API_KEY
        if "model" in input_data:
            API_MODEL = input_data["model"]
        if "api_base_url" in input_data:
            API_BASE_URL = input_data["api_base_url"]
        if "api_key" in input_data:
            API_KEY = input_data["api_key"]

        # 优先模型：新字段 preferred_model 优先，旧字段 model 兜底（向后兼容）
        preferred = str(input_data.get("preferred_model") or API_MODEL or "").strip()
        _apply_pool_overrides()

        # 诊断行必须与 _build_prompt 的实际选路一致：
        # 用同一套归一化（strip 后非空才算"是"），注入条数也只数真正会被写入的
        print(f"[model_fallback] 定向回复={'是' if _target_content(target) else '否'}"
              f" | 防复读注入={len(_injected_recent(avoid_recent))}条",
              file=sys.stderr, flush=True)

        reply = asyncio.run(generate_reply(context, persona, max_tokens, preferred,
                                           target, avoid_recent))
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
