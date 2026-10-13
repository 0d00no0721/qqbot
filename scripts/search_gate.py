# -*- coding: utf-8 -*-
"""搜索判定器 —— T0 显式指令 > T1 强规则 > T2 路由器 > 默认不搜。

- T0：以 搜索/搜一下/查一下/查询/search 开头（可带前导 @某人/[CQ:at]）→ 一定搜，
  查询词 = 去掉指令词后的剩余文本；剩余为空 → 不搜。
- T1：实时词（天气/新闻/股价/比分/…）× 疑问特征（？/吗/怎么/多少/…），
  或直接命中"自足强词"（谁赢了/比分/股价/多少钱/几点/…）→ 搜，查询词 = 整条消息。
- T2：一次极短模型池调用（temperature=0，只允许输出 `SEARCH: <查询>` 或 `NO`）；
  超时 / 异常 / 解析失败 / 乱输出 → **不搜**（fail-closed）。
- 默认不搜。自动搜索只在 bot_mentioned 时被调用（调用方负责）。

限流：每群滑动窗口（默认 10 次/小时，可配；<=0 视作硬闸一律拒绝）。
配置：config/search.json 按 mtime 热加载，改参数不必重启。

正文归一：判定与生成 query 之前，先 `_strip_lead`（去 @某人/[CQ:at]）→ `_strip_quote`
（剥引用块 `[回复消息 [昵称(QQ号)] 被引用内容]`，只有末尾一个 `]`，且被引用内容自身
可能含 `]`，故按括号配对而非"找第一个 ]"）→ 再 `_strip_lead` 清引用块后面的 @。**引用
内容不参与触发判定，也不进 query**。

冻结契约：
    await decide(text, gid, *, feature_on=True)
        -> {"search": bool, "query": str, "tier": "T0"|"T1"|"T2"|"none", "reason": str,
            "time_range": "day"|"week"|None}
        time_range 为后加键（向后兼容：现有键一个没删没改），供调用方透传给
        web_search.search(..., time_range=…)；无实时性暗示时为 None。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "search.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "tiers": {"t0": True, "t1": True, "t2": True},
    "rate_limit_per_hour": 10,
    "max_results": 5,
    "timeout_seconds": 15,
    "router_max_tokens": 24,
    "router_timeout_seconds": 8,
    "interim_notice": False,
    "base_url": "",
}

RATE_WINDOW_SECONDS = 3600.0
T2_MIN_CHARS = 3            # T2 廉价前置闸：太短的消息不值得花一次模型调用

# ---- T0 ----
_T0_KEYWORDS = ("搜一下", "搜索", "查一下", "查询", "search")
_CQ_AT_RE = re.compile(r"^\s*\[CQ:at,[^\]]*\]\s*")
_MENTION_RE = re.compile(r"^\s*@[^\s@]+[\s:：,，]*")
_MENTION_QQ_TAIL_RE = re.compile(r"^\(\d{5,}\)[\s:：,，]*")   # 日志里的 "@昵称 (123456)" 尾巴

# ---- T1 ----
# 只留**无歧义实时词**：135 条真实群消息回放证明，宽表里的
# 最新/今天/现在/目前/实时/价格/版本/排名/上市/获奖/选举… 在技术群里是闲聊高频词，
# 一律会误报（详见 qq_native_re/findings/search/v1_polish/V1p_polish.md）。
_REALTIME_WORDS = (
    "天气", "气温", "预报", "新闻", "股价", "涨跌", "汇率", "比分", "赛程",
    "发布会", "谁赢", "开服", "公测", "限号", "彩票",
)
_QUESTION_WORDS = (
    "？", "?", "吗", "呢", "怎么", "多少", "哪个", "哪些", "什么时候", "几点",
    "谁", "为什么", "是不是", "有没有", "多久",
)
# 去掉 什么时候/版本号/最新版/更新内容（闲聊里单独出现太常见，实测误报）。
_STRONG_WORDS = (
    "谁赢了", "谁赢", "比分", "股价", "汇率", "多少钱", "几点", "发布会",
)

# ---- 时间范围（P4：透传给 bridge 的 timeRange；实测会从 bing 换到 exa，结果更新鲜）----
_TIME_DAY_WORDS = ("今天", "今日", "本日", "当天", "最新", "刚刚", "现在", "目前", "实时")
# P4-2：由 ("本周","这周") 扩为含 最近/近期/这几天/这阵子。
# 安全边界（很关键）：这些词只影响**已经触发搜索**时的 time_range（进而影响 web_search
# 选哪个引擎），**不会增多触发** —— T1 的 _REALTIME_WORDS/_STRONG_WORDS 里没有它们，
# 而 T2 路由器与默认不搜的路径与 time_range 无关。真机证据：`原神最近的更新` 走
# time_range=None → bing → 混入百度百科"原"字；带上时间词后落 exa → 官网/社区/卡池。
_TIME_WEEK_WORDS = ("本周", "这周", "最近", "近期", "这几天", "这阵子")

# ---- T2 ----
_ROUTER_SYSTEM = (
    "你是联网搜索判定器。只有必须联网查实时或外部事实才能回答时才需要搜索。"
    "严格只输出一行：SEARCH: <检索词> 或 NO。不要解释，不要多余文字。"
)
_SEARCH_LINE_RE = re.compile(r"^\s*SEARCH\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)
_NO_LINE_RE = re.compile(r"^\s*NO\s*[.!。！]?\s*$", re.IGNORECASE)

# ---- 限流 ----
_rate_lock = threading.Lock()
_rate_usage: dict = {}
_cfg_cache = (-1.0, {})

def load_config() -> dict:
    """读 config/search.json（mtime 热加载）；缺失/损坏 → 内置默认值。"""
    global _cfg_cache
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return json.loads(json.dumps(DEFAULT_CONFIG))
    if _cfg_cache[0] == mtime and isinstance(_cfg_cache[1], dict) and _cfg_cache[1]:
        return json.loads(json.dumps(_cfg_cache[1]))
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        for key, val in raw.items():
            if key == "tiers" and isinstance(val, dict):
                cfg["tiers"].update({k: bool(v) for k, v in val.items()})
            else:
                cfg[key] = val
    _cfg_cache = (mtime, cfg)
    return json.loads(json.dumps(cfg))

def _int_cfg(cfg: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        val = int(cfg.get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))

def _float_cfg(cfg: dict, key: str, default: float, low: float, high: float) -> float:
    try:
        val = float(cfg.get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))

def _rate_counts(gid: str, window: float = RATE_WINDOW_SECONDS) -> int:
    """窗口内已搜次数（顺带清理过期记录，不占额）。"""
    key = str(gid or "")
    now = time.time()
    with _rate_lock:
        buf = _rate_usage.setdefault(key, [])
        cutoff = now - window
        while buf and buf[0] < cutoff:
            buf.pop(0)
        return len(buf)

def _rate_commit(gid: str, window: float = RATE_WINDOW_SECONDS) -> int:
    """记一次搜索（先问后搜：问过没搜也算占额）。"""
    key = str(gid or "")
    now = time.time()
    with _rate_lock:
        buf = _rate_usage.setdefault(key, [])
        cutoff = now - window
        while buf and buf[0] < cutoff:
            buf.pop(0)
        buf.append(now)
        return len(buf)

def _rate_reset(gid: Optional[str] = None) -> None:
    """清空限流计数（仅演练/测试用）。"""
    with _rate_lock:
        if gid is None:
            _rate_usage.clear()
        else:
            _rate_usage.pop(str(gid or ""), None)

def _strip_lead(text: str) -> str:
    """去掉开头的 [CQ:at,…] / @某人（含日志形态的 `@昵称 (123456)`）与空白，保留正文。"""
    out = str(text or "")
    while True:
        new = _CQ_AT_RE.sub("", out, count=1)
        new = _MENTION_RE.sub("", new, count=1)
        new = _MENTION_QQ_TAIL_RE.sub("", new, count=1)
        if new == out:
            break
        out = new
    return out.strip()

# ---- 引用块（真实格式，群日志实证）----
#   [回复消息 [某甲(123)] 今天天气如何] 这个我不太清楚
# 注意两点（第一版正则就栽在这）：
#   1) **只有末尾一个 `]`**，写成 `]]` 会整条漏剥；
#   2) 被引用内容自身可能以 `]` 结尾，例如
#      `[回复消息 [游云不喜欢哦(10001)] [图片]] 这玩意儿是啥？`
#      → 必须按括号配对找"深度归零"的那个 `]`，不能取第一个 `]`。
_REPLY_HEAD = "[回复消息"
_CQ_ANY_RE = re.compile(r"\[CQ:[^\]]*\]")
_WS_ANY_RE = re.compile(r"\s+")

def _bracket_end(s: str, start: int) -> int:
    """s[start] 必须是 `[`；返回配对块尾后一位索引（括号未闭合 → 吃掉整段）。"""
    depth = 0
    for i in range(start, len(s)):
        ch = s[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth <= 0:
                return i + 1
    return len(s)

def _strip_quote(text: str) -> str:
    """剥掉开头的 `[回复消息 …]` 引用块与残留 `[CQ:…]`，返回供判定/生成 query 的正文。"""
    out = str(text or "")
    while True:
        s = out.lstrip()
        if not s.startswith(_REPLY_HEAD):
            out = s
            break
        out = s[_bracket_end(s, 0):]          # 连同配对的收尾 `]` 一起吃掉
    out = _CQ_ANY_RE.sub(" ", out)            # [CQ:reply,…] / [CQ:at,…] / [CQ:image,…] 等
    return _WS_ANY_RE.sub(" ", out).strip()

def _body_of(text: str) -> str:
    """判定统一入口的正文：去 @/CQ:at → 剥引用块 → 再清一次 @（引用块后面常跟 @bot）。"""
    return _strip_lead(_strip_quote(_strip_lead(text)))

def _time_range_of(body: str) -> Optional[str]:
    """P4：正文的时间暗示 → bridge 的 timeRange（"day"/"week"/None）。"""
    text = str(body or "")
    if any(w in text for w in _TIME_DAY_WORDS):
        return "day"
    if any(w in text for w in _TIME_WEEK_WORDS):
        return "week"
    return None

def _t0_match(text: str) -> tuple:
    """(是否命中显式指令, 剩余查询词)。"""
    low = text.lower()
    for kw in _T0_KEYWORDS:
        if low.startswith(kw):
            rest = text[len(kw):]
            return True, rest.lstrip(" \t:：,，。.、~～-—!！?？").strip()
    return False, ""

def _t1_hit(text: str) -> tuple:
    """(是否命中强规则, 命中说明)。"""
    for word in _STRONG_WORDS:
        if word in text:
            return True, word
    rt = next((w for w in _REALTIME_WORDS if w in text), "")
    qt = next((w for w in _QUESTION_WORDS if w in text), "")
    if rt and qt:
        return True, rt + "+" + qt
    return False, ""

def _parse_router_output(raw: str) -> Optional[str]:
    """严格解析：只看前 3 个非空行；`SEARCH: <查询>` 才算命中，NO/其它 → None。"""
    lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
    for line in lines[:3]:
        if _NO_LINE_RE.match(line):
            return None
        m = _SEARCH_LINE_RE.match(line)
        if m:
            query = m.group(1).strip().strip("\"'“”「」《》")
            return query or None
    return None

def _load_pool():
    try:
        from scripts import llm_pool
    except ImportError:
        import llm_pool
    return llm_pool

async def _router_raw(text: str, cfg: dict) -> tuple:
    """调用模型池做一次极短判定，返回 (原始输出, 模型名, 错误说明)。"""
    max_tokens = _int_cfg(cfg, "router_max_tokens", 24, 8, 512)
    timeout = _float_cfg(cfg, "router_timeout_seconds", 8.0, 0.5, 60.0)
    try:
        pool = _load_pool()
    except Exception as exc:
        return "", "", "模型池不可用（%s）" % type(exc).__name__
    try:
        raw, model = await asyncio.wait_for(
            pool.chat(
                [
                    {"role": "system", "content": _ROUTER_SYSTEM},
                    {"role": "user", "content": text},
                ],
                label="search_router",
                max_tokens=max_tokens,
                temperature=0.0,
                timeout=timeout,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return "", "", "路由器超时（>%gs）" % timeout
    except Exception as exc:
        return "", "", "路由器异常（%s: %s）" % (type(exc).__name__, str(exc)[:80])
    return str(raw or ""), str(model or ""), ""

async def _router_query(text: str, cfg: dict) -> tuple:
    """(查询词 或 None, 说明)；任何失败 → (None, 原因)，fail-closed。"""
    raw, model, err = await _router_raw(text, cfg)
    if err:
        return None, err
    query = _parse_router_output(raw)
    if not query:
        return None, "路由器输出未命中（%s）" % (raw.strip()[:60] or "空输出")
    return query, "路由器命中（model=%s）" % (model or "?")

def _result(search: bool, query: str, tier: str, reason: str,
            time_range: Optional[str] = None) -> dict:
    return {"search": bool(search), "query": str(query or ""), "tier": tier,
            "reason": reason, "time_range": time_range}

async def _decide_impl(text: str, gid: str, feature_on: bool) -> dict:
    cfg = load_config()
    body = _body_of(text)
    tr = _time_range_of(body)
    tiers = cfg.get("tiers") if isinstance(cfg.get("tiers"), dict) else {}
    tier = "none"
    query = ""
    reason = ""

    if not cfg.get("enabled", True):
        res = _result(False, "", "none", "搜索总开关关闭（config/search.json enabled=false）", tr)
    elif not feature_on:
        res = _result(False, "", "none", "本群未开启 search 功能", tr)
    else:
        if tiers.get("t0", True):
            hit, rest = _t0_match(body)
            if hit:
                if rest:
                    tier, query, reason = "T0", rest, "显式指令"
                else:
                    reason = "T0 指令后没有查询内容"
        if tier == "none" and not reason and tiers.get("t1", True):
            hit, word = _t1_hit(body)
            if hit:
                tier, query, reason = "T1", body, "实时词规则命中（%s）" % word
        if tier == "none" and not reason and tiers.get("t2", True) and len(body) >= T2_MIN_CHARS:
            limit = _int_cfg(cfg, "rate_limit_per_hour", 10, 0, 100000)
            used = _rate_counts(gid)
            if limit <= 0 or used >= limit:
                reason = "限流：本群 1 小时内已搜 %d/%d 次" % (used, limit)
            else:
                q, why = await _router_query(body, cfg)
                if q:
                    tier, query, reason = "T2", q, why
                else:
                    reason = why
        if tier == "none":
            res = _result(False, "", "none", reason or "未命中任何触发条件（默认不搜）", tr)
        else:
            limit = _int_cfg(cfg, "rate_limit_per_hour", 10, 0, 100000)
            used = _rate_counts(gid)
            if limit <= 0 or used >= limit:
                res = _result(False, query, tier,
                              "限流：本群 1 小时内已搜 %d/%d 次（上限 %d）" % (used, limit, limit), tr)
            else:
                _rate_commit(gid)
                res = _result(True, query, tier, reason, tr)

    print("[搜索] 判定=%s 层=%s 查询=%s 时间=%s 原因=%s"
          % ("搜" if res["search"] else "不搜", res["tier"], res["query"],
             res["time_range"] or "-", res["reason"]), flush=True)
    return res

async def decide(text: str, gid: str, *, feature_on: bool = True) -> dict:
    """三层判定；决策本身**不抛异常**（意外异常 → 保守地不搜）。"""
    try:
        return await _decide_impl(text, gid, feature_on)
    except Exception as exc:
        try:
            tr = _time_range_of(_body_of(text))
        except Exception:
            tr = None
        print("[搜索] 判定=不搜 层=none 查询= 原因=判定器异常（%s: %s）"
              % (type(exc).__name__, str(exc)[:80]), flush=True)
        return _result(False, "", "none", "判定器异常（%s）" % type(exc).__name__, tr)
