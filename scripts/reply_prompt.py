# -*- coding: utf-8 -*-
"""聊天提示词与纯文本清理，主进程及独立调试脚本共用。"""

import re

_NICKNAME_PREFIX = re.compile(r'^\[.+?\][:：]\s*')


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


def clean_reply(content):
    return _NICKNAME_PREFIX.sub("", (content or "").strip()).strip()
