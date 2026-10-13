# -*- coding: utf-8 -*-
"""联网搜索后端 —— 借 DSH `dsh-free-search` 插件的 loopback HTTP bridge。

唯一出口：POST {base}/raw-search，base 默认
    http://127.0.0.1:19387/api/dsh-free-search-settings
- 插件只允许 loopback（本机请求天然满足），无需 key / 登录；
- 插件自带 5 分钟缓存与引擎回退链（默认 bing）；
- 本模块 **永不抛异常**：连接拒绝 / 超时 / 非 200 / JSON 坏 / ok=false / sources 空
  一律收敛成 ok=False + 中文 reason，调用方永远拿到 dict —— 搜索挂了只退化成
  "查不到"，不影响机器人其它功能。

冻结契约：
    await search(query, max_results=5, time_range=None, *, with_pages=True) -> dict
        {"ok","provider","sources":[{"title","url","snippet","raw_title","raw_snippet",
                                     "page_ok","page_chars"}],
         "content","cache","reason","elapsed_ms","page_elapsed_ms","pages_fetched"}
        **snippet = 「给模型看的最佳可用文本」**：抓到的网页正文优先，抓不到才回退搜索摘要。
        只注入搜索摘要答不了实时问题（摘要往往是页面模板文字，没有具体数字）；抓正文后
        资料块里才有真实数据（实测天气页正文含 24℃/25℃）。
        raw_title/raw_snippet = 清洗前原文，仅供排查（后加键，向后兼容）；
        **raw_snippet 永远保存原始搜索摘要，不被抓到的正文覆盖**。
        page_ok/page_chars = 该条是否抓到正文、正文字数（后加键，向后兼容）。
        elapsed_ms = 本次 search() 内**所有 bridge 往返**的耗时；未走质量闸重试时
        （绝大多数查询）与改造前逐字一致，走重试时是首次+重试两趟之和（后加键
        first_elapsed_ms / retry_elapsed_ms 给出拆分）。
        page_elapsed_ms = 抓正文环节耗时（后加键）。
        with_pages=False → 完全跳过抓正文（纯搜索摘要，行为等同改造前）。

        **质量闸 + 一次 exa 重试（P4-2，后加键，向后兼容）**：调用方**未显式给
        time_range** 且首次结果相关度（`_relevance`，query 词元命中比例，title 优先）
        低于 config `quality_retry_min_relevance`（默认 0.34）时，自动以
        `time_range="month"`（实测触发 exa）**重试一次**；重试成功且相关度**更高**
        → 采用重试结果。触发原因见 qq_native_re/findings/search/v1_quality/V1q_quality.md。
            retried        True = 最终**采用**了重试结果（否则 False）
            retry_reason   文字说明；**空串 = 本次根本没重试**（质量闸未触发）
            first_provider 首次结果（重试前）的 provider；provider 始终是最终采用的
            relevance      质量闸判定时（**抓正文前**）最终源集的相关度 0~1
            first_relevance / retry_elapsed_ms / first_elapsed_ms  证据用拆分
        约束：最多重试 1 次；重试异常/超时 → 静默保留首次结果，**仍不抛异常**；
        抓正文只对最终源集执行（弃用的首次结果不会被白抓）。
        title 仍为 _clean_text() 清洗后文本（解 HTML 实体 + 去标签 + 折空白）。

    await fetch_page(url, *, max_chars=None, timeout=None) -> dict
        {"ok","text","chars","reason","elapsed_ms"}；**永不抛异常**。
        **URL 守卫**：仅允许 http/https；主机名解析后若落在 loopback/私有/链路本地/
        保留网段（127/8、::1、10/8、172.16/12、192.168/16、169.254/16、0.0.0.0/8、
        100.64/10、198.18/15、240/4、fc00::/7、fe80::/10 等）→ ok=False
        reason="拒绝内网地址"，且**不发起任何请求**（SSRF 屏蔽：实测改造前抓
        http://127.0.0.1:19387/… 能拿到本机响应，必须堵死）。

    await enrich_sources(sources, *, limit=None, max_chars=None) -> list
        对前 limit 条（默认 config/search.json 的 fetch_pages，默认 3）**并行**抓正文
        （asyncio.gather；单页 0.9–2.8s，串行会拖死回复链）。成功且正文非空 → 该条
        snippet 替换为正文、page_ok=True、page_chars=N；失败/正文空 → **原样保留 snippet**、
        page_ok=False（优雅降级，反爬 403 页也不例外）。整体 asyncio.wait_for 总闸
        （默认 10s）：超时用已完成的结果、其余保留原摘要。limit<=0 → 原样返回（不新增键）。
    环境变量（调试用，优先于 config/search.json）：DSH_SEARCH_BASE_URL / DSH_SEARCH_TIMEOUT
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "search.json"

DEFAULT_BASE_URL = "http://127.0.0.1:19387/api/dsh-free-search-settings"
DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RESULTS = 5
MAX_RESULTS_CAP = 10
MIN_TIMEOUT = 0.05
MAX_TIMEOUT = 60.0

# ---- 质量闸 + 一次重试（config: quality_retry_min_relevance）----
# 真机问题（P4-2）：`search("原神 7.1 版本 up池 角色", 5)` 不传 timeRange → bridge 默认
# bing → 前 5 条混入「原（汉语文字）_百度百科」「原的拼音/部首/笔顺」这类**按单字**
# 返回的垃圾；同一查询带 timeRange → 落到 exa → 前 5 条全是「7.1版本up池角色全解析」。
# 根因是 time_range 只在命中少数时间词时才带（search_gate），大量查询因此落到 bing。
# 修法：不猜词表，按**结果相关度**兜底 —— 首次相关度低就用 month 重试一次（触发 exa）。
DEFAULT_QUALITY_MIN_RELEVANCE = 0.34   # 低于此值才重试（0 → 关闭，1 → 近乎总重试）
QUALITY_RETRY_TIME_RANGE = "month"      # 重试时带的 timeRange（实测 day/week/month 结果一致）
QUALITY_RETRY_TIMEOUT = 6.0             # 重试单次硬上限（秒）：额外耗时可控，不会拖死回复链

# ---- 抓正文（fetch_page / enrich_sources）----
DEFAULT_FETCH_PAGES = 3          # 抓前 N 条（config: fetch_pages）
DEFAULT_PAGE_MAX_CHARS = 3000    # 每条正文截断字数（config: page_max_chars）
# 2026-10-13 由 1200 上调到 3000：PM 终验实测天气页温度出现在正文第 307/484/**1386** 字处，
# 1200 会漏掉后文真实数据（见 qq_native_re/findings/search/PM_ACCEPTANCE.md §二）。
# 该默认值仅在 config/search.json 缺该键时生效；与 live 配置（3000）保持一致，
# 避免"配置键被删就静默退回旧值"这类难查的行为漂移。
DEFAULT_PAGE_TIMEOUT = 8.0       # 单页超时秒（config: page_timeout_seconds）
DEFAULT_PAGE_TOTAL_TIMEOUT = 10.0  # 整批抓取总闸秒（asyncio.wait_for）
MIN_PAGE_MAX_CHARS = 100
MAX_PAGE_MAX_CHARS = 20000
MIN_PAGE_TIMEOUT = 0.5
MAX_PAGE_TIMEOUT = 30.0
DNS_TIMEOUT = 3.0                # 守卫里的域名解析也要有上限，不能拖住事件循环
# 浏览器 UA：部分站点对默认 python-httpx UA 直接 403
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_cfg_cache = (-1.0, {})

def _config() -> dict:
    """读 config/search.json（mtime 热加载，改参数不必重启）；缺失/坏文件 → {}。"""
    global _cfg_cache
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache[0] == mtime:
        return _cfg_cache[1]
    data: dict = {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except Exception:
        data = {}
    _cfg_cache = (mtime, data)
    return data

def _base_url() -> str:
    env = (os.getenv("DSH_SEARCH_BASE_URL") or "").strip()
    if env:
        return env.rstrip("/")
    cfg = str(_config().get("base_url") or "").strip()
    return cfg.rstrip("/") if cfg else DEFAULT_BASE_URL

def _timeout() -> float:
    env = (os.getenv("DSH_SEARCH_TIMEOUT") or "").strip()
    val: Any = env if env else _config().get("timeout_seconds")
    try:
        sec = float(val)
    except (TypeError, ValueError):
        sec = DEFAULT_TIMEOUT
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, sec))

def _log(msg: str) -> None:
    try:
        print("[搜索] " + msg, flush=True)
    except Exception:
        pass

# ---- 抓正文相关配置（config/search.json 热加载；缺失/坏值 → 内置默认）----
def _cfg_int(key: str, default: int, low: int, high: int) -> int:
    try:
        val = int(_config().get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))

def _cfg_float(key: str, default: float, low: float, high: float) -> float:
    try:
        val = float(_config().get(key, default))
    except (TypeError, ValueError):
        val = default
    return max(low, min(high, val))

def _fetch_pages() -> int:
    """抓前几条结果的正文（0 = 关闭抓正文，行为退回纯 snippet）。"""
    return _cfg_int("fetch_pages", DEFAULT_FETCH_PAGES, 0, MAX_RESULTS_CAP)

def _page_max_chars() -> int:
    """单条正文截断字数。"""
    return _cfg_int("page_max_chars", DEFAULT_PAGE_MAX_CHARS, MIN_PAGE_MAX_CHARS, MAX_PAGE_MAX_CHARS)

def _page_timeout() -> float:
    """单页超时秒（实测 192.168.1.1 会 ConnectTimeout 12.2s，故默认收到 8s）。"""
    return _cfg_float("page_timeout_seconds", DEFAULT_PAGE_TIMEOUT, MIN_PAGE_TIMEOUT, MAX_PAGE_TIMEOUT)

def _page_total_timeout(page_timeout: float) -> float:
    """整批抓取总闸：默认 10s；单页超时被调大时总闸跟着放大，避免总闸先掐掉单页。"""
    return max(DEFAULT_PAGE_TOTAL_TIMEOUT, float(page_timeout) + 2.0)

# ================= 质量闸：相关度打分（决定要不要重试一次） =================
# 词元切分（两级）：
#   ① 先按空白/标点切块（`[\w.+#]+`：`.+#` 是为了保住 "7.1"/"C++"/"C#"，
#      否则 "7.1" 会被切成 "7"/"1" 两个 <2 碎片而整体丢失）；
#   ② 块内再按**书写系统**切成 CJK 段 / 字母数字段（关键：中文常与英文数字连写，
#      "原神7.1版本up池角色" 是一个块，不切就永远匹配不上任何标题）；
#   ③ 每段：纯 CJK 且长度 >2 → 滑窗 bigram；其余（含字母数字）长度 ≥2 原样保留。
# 为什么非做 bigram 不可（实测反例，见 V1q_quality.md §2）：
#   中文查询常**没有空格**，"今天深圳天气" 是一个 6 字段。若整段当唯一词元，
#   它在「【深圳今天天气预报】深圳天气预报24小时详情」里**不算命中**（字序不同）
#   → 相关度 0.00 → 白触发一次重试（把好查询也拖慢）。切成 bigram 后同一页面
#   命中 今天/天深/深圳/圳天/天气，相关度 0.80，稳定不重试。
# 度量选型（同一批真机样本上比过 3 种，见 _v1q_metric_lab.py + _v1q_samples.json）：
#   字级覆盖率被否 —— 它给**实测垃圾**打 0.356（> 0.34）⇒ 永远不触发，等于没闸；
#   整块不切被否 —— 无空格查询恒 0.000（好结果也 0.000，重试也救不回来）；
#   最终采用「按书写系统切 + bigram + title 计 2 分/snippet 计 1 分 + 逐条平均」。
_CHUNK_RE = re.compile(r"[\w.+#]+", re.UNICODE)
_SCRIPT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+"
                        r"|[^\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_ASCII_ALNUM_RE = re.compile(r"[0-9A-Za-z]")

def _quality_min_relevance() -> float:
    """质量闸阈值（config: quality_retry_min_relevance，默认 0.34；坏值/越界 → 默认值）。"""
    return _cfg_float("quality_retry_min_relevance", DEFAULT_QUALITY_MIN_RELEVANCE, 0.0, 1.0)

def _expand_piece(piece: str) -> list:
    """一段（同书写系统）→ 词元：纯 CJK 长段切 bigram，其余长度 ≥2 原样保留。"""
    piece = piece.strip(".")
    if len(piece) < 2:
        return []
    if len(piece) > 2 and not _ASCII_ALNUM_RE.search(piece):
        return [piece[i:i + 2] for i in range(len(piece) - 1)]
    return [piece]

def _query_terms(query: str) -> list:
    """查询 → 词元列表（小写、去重、丢长度<2 的碎片）。永不抛异常。"""
    out: list = []
    try:
        chunks = _CHUNK_RE.findall(str(query or "").lower())
        for chunk in chunks:
            for piece in _SCRIPT_RE.findall(chunk):
                out.extend(_expand_piece(piece))
    except Exception:
        return []
    seen: set = set()
    uniq: list = []
    for term in out:
        if term not in seen:
            seen.add(term)
            uniq.append(term)
    return uniq

def _relevance(query: str, sources: Any) -> float:
    """query 词元在结果里的命中比例（0~1）；**title 优先**：命中 title 计 2 分、仅命中 snippet 计 1 分。

    逐条算"这条结果覆盖了多少 query 词元"，再对条数取平均 —— 语义是"平均每条结果
    有多贴题"。刻意**不**用"整个词表是否在某一条里出现过"：那会把"网页 A 命中前半、
    网页 B 命中后半"也算成高分（实测 bing 的"原神"垃圾结果就是这么被抬起来的）。
    无词元 / 无结果 / 任何异常 → 1.0（**没有判据就不重试**，不白花一次搜索）。
    """
    try:
        terms = _query_terms(query)
        items = [s for s in (sources or []) if isinstance(s, dict)]
        if not terms or not items:
            return 1.0
        total = 0.0
        for src in items:
            title = str(src.get("title") or "").lower()
            snippet = str(src.get("snippet") or "").lower()
            score = 0.0
            for term in terms:
                if term in title:
                    score += 2.0
                elif term in snippet:
                    score += 1.0
            total += score / (2.0 * len(terms))
        return total / len(items)
    except Exception:
        return 1.0

def _fail(reason: str, provider: str = "", cache: str = "", elapsed_ms: int = 0) -> dict:
    return {"ok": False, "provider": provider, "sources": [], "content": "",
            "cache": cache, "reason": reason, "elapsed_ms": int(elapsed_ms),
            "page_elapsed_ms": 0, "pages_fetched": 0}

# ---- 文本清洗（bridge 返回的 title/snippet 带 HTML 实体与 <b> 高亮标签）----
# 实测样例（bridge 原文）：
#   "22 小时之前&ensp;·&ensp;深圳天气预报"  "1 天前&ensp;&middot;&ensp;…"
#   "…<b>深圳</b>天气…"
_NAMED_ENTITIES = (
    ("&ensp;", " "), ("&emsp;", " "), ("&nbsp;", " "), ("&thinsp;", " "),
    ("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"), ("&apos;", "'"),
    ("&middot;", "·"), ("&lt;", "<"), ("&gt;", ">"), ("&sdot;", "⋅"),
    ("&mdash;", "—"), ("&ndash;", "–"), ("&hellip;", "…"), ("&bull;", "•"),
    ("&ldquo;", "“"), ("&rdquo;", "”"), ("&lsquo;", "‘"), ("&rsquo;", "’"),
    ("&times;", "×"), ("&copy;", "©"), ("&reg;", "®"), ("&deg;", "°"),
    ("&laquo;", "«"), ("&raquo;", "»"),
)
_NUM_ENTITY_RE = re.compile(r"&#(\d{1,7});")
_HEX_ENTITY_RE = re.compile(r"&#[xX]([0-9a-fA-F]{1,6});")
_BR_TAG_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
# 只清高亮/排版类白名单标签，避免误伤正文里的 `<https://…>` 之类
_TAG_RE = re.compile(
    r"</?\s*(?:b|strong|em|i|u|s|del|ins|span|font|mark|small|big|sub|sup|"
    r"code|kbd|abbr|cite|q|a|p|div|ul|ol|li|h[1-6])\b[^<>]*>",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"[\s\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff]+")

def _decode_codepoint(num: str, base: int) -> str:
    """数字实体 → 字符；控制符/代理区/越界一律丢弃（避免脏字符污染下游）。"""
    try:
        cp = int(num, base)
    except (TypeError, ValueError):
        return ""
    if cp in (0x09, 0x0A, 0x0D):
        return " "
    if cp < 0x20 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
        return ""
    return chr(cp)

def _decode_entities(s: str) -> str:
    """解 HTML 实体（命名 + `&#NN;` + `&#xNN;`）；`&deg;`→`°`、`&#8451;`→`℃` 都在这层出。"""
    s = _NUM_ENTITY_RE.sub(lambda m: _decode_codepoint(m.group(1), 10), s)
    s = _HEX_ENTITY_RE.sub(lambda m: _decode_codepoint(m.group(1), 16), s)
    for ent, ch in _NAMED_ENTITIES:
        if ent in s:
            s = s.replace(ent, ch)
    return s

def _clean_text(value: Any) -> str:
    """解 HTML 实体（命名 + `&#NN;` + `&#xNN;`）→ 去标签 → 折叠连续空白。永不抛异常。"""
    try:
        s = "" if value is None else str(value)
    except Exception:
        return ""
    if not s:
        return ""
    s = _decode_entities(s)
    s = _BR_TAG_RE.sub(" ", s)
    s = _TAG_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()

def _norm_sources(raw: Any) -> list:
    """规范化 sources：title/snippet 走 _clean_text() 清洗，另存 raw_* 原文供排查；
    snippet 缺失补空串；没有 title 也没有 url 的条目丢弃。"""
    out: list = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            raw_title = "" if item.get("title") is None else str(item.get("title"))
            raw_snippet = "" if item.get("snippet") is None else str(item.get("snippet"))
            title = _clean_text(raw_title)
            snippet = _clean_text(raw_snippet)
            if not (url or title):
                continue
            out.append({"title": title, "url": url, "snippet": snippet,
                        "raw_title": raw_title, "raw_snippet": raw_snippet})
    return out

# ================= 抓网页正文（SSRF 屏蔽 + 并行 + 总闸） =================

# ---- 正文提取 ----
# 顺序刻意与"先折空白再解实体"相反：`&nbsp;` 解出来是 \xa0，必须先解实体再折空白，
# 否则页面上大量 &nbsp; 会以裸不换行空格留在正文里。
_STRIP_BLOCK_RE = re.compile(
    r"<\s*(script|style|noscript|svg|template|iframe)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# 未闭合的 <script>/<style> 会吃掉整页文字，单独再切一刀（切到文末）
_UNCLOSED_BLOCK_RE = re.compile(
    r"<\s*(script|style|noscript|svg|template|iframe)\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ANY_TAG_RE = re.compile(r"<[^>]*>")
_CHARSET_RE = re.compile(rb"""charset\s*=\s*["']?\s*([A-Za-z0-9_\-]+)""")

def _decode_html(raw: bytes, resp_text: str) -> str:
    """按页面自报的 charset 解码（中文站点常见 gb2312/gbk）；没把握就用 httpx 的结果。"""
    two_kb = raw[:4096] if isinstance(raw, (bytes, bytearray)) else b""
    m = _CHARSET_RE.search(two_kb)
    if not m:
        return resp_text
    enc = m.group(1).decode("ascii", "ignore").lower()
    if enc in ("gb2312", "gbk", "gb18030"):
        enc = "gb18030"
    if enc in ("utf-8", "utf8", "ascii", "iso-8859-1", "latin-1"):
        return resp_text                      # httpx 已按同一 charset 解过，不必重复
    try:
        alt = raw.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return resp_text
    # 只有明显更干净才换（防"错误编码恰好不产生 U+FFFD"的误判）
    return alt if alt.count("\ufffd") < resp_text.count("\ufffd") else resp_text

def _extract_text(html: Any, max_chars: int) -> str:
    """HTML → 纯文本：去 script/style/noscript/svg → 去注释 → 去标签 → 解实体 → 折空白 → 截断。"""
    s = "" if html is None else str(html)
    if not s:
        return ""
    s = _STRIP_BLOCK_RE.sub(" ", s)
    s = _UNCLOSED_BLOCK_RE.sub(" ", s)
    s = _HTML_COMMENT_RE.sub(" ", s)
    s = _ANY_TAG_RE.sub(" ", s)               # 去标签放在解实体之前，避免 &lt;script&gt; 被误删
    s = _decode_entities(s)
    s = _WS_RE.sub(" ", s).strip()
    if max_chars and len(s) > max_chars:
        s = s[:max_chars].rstrip()
    return s

# ---- URL 守卫（SSRF 屏蔽；命中即拒绝，绝不发请求） ----
_ALLOWED_SCHEMES = ("http", "https")
_REJECT_INTERNAL = "拒绝内网地址"
# 显式补充网段：各 Python 版本 ipaddress 的 is_private 覆盖范围有差异（100.64/10 等
# 是后来才并入的），所以不依赖单一属性，额外把常见"非公网"段列一遍。
_EXTRA_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4", "255.255.255.255/32",
    "::/128", "64:ff9b::/96", "100::/64", "2001:db8::/32", "fc00::/7", "fe80::/10",
))

def _ip_verdict(ip: Any) -> str:
    """IP → 拒绝原因（空串 = 放行）。loopback/私有/链路本地/保留/组播/未指定一律拒。"""
    mapped = getattr(ip, "ipv4_mapped", None)      # ::ffff:127.0.0.1 之类按 IPv4 语义判
    if ip.version == 6 and mapped is not None:
        ip = mapped
    if (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified):
        return _REJECT_INTERNAL
    for net in _EXTRA_BLOCKED_NETS:
        if ip.version == net.version and ip in net:
            return _REJECT_INTERNAL
    return ""

async def _resolve_ips(host: str) -> list:
    """主机名 → IP 列表（在事件循环里做，带 DNS_TIMEOUT 上限，不阻塞别人）；IP 字面量不走 DNS。"""
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, None, type=socket.SOCK_STREAM), timeout=DNS_TIMEOUT)
    out: list = []
    for info in infos:
        addr = str(info[4][0])
        if addr not in out:
            out.append(addr)
    return out

async def _url_guard(url: str) -> str:
    """URL 守卫：返回拒绝原因（空串 = 放行）。**全部判定都在发请求之前完成**。"""
    s = str(url or "").strip()
    if not s:
        return "URL 为空"
    try:
        parts = urlsplit(s)
    except Exception:
        return "URL 无法解析"
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return "拒绝非 http(s) 协议（%s）" % (scheme or "无")
    host = parts.hostname or ""
    if not host:
        return "URL 缺少主机名"
    try:
        ips = await _resolve_ips(host)
    except asyncio.TimeoutError:
        return "域名解析超时（>%gs）" % DNS_TIMEOUT
    except Exception as exc:
        return "域名无法解析（%s）" % type(exc).__name__
    if not ips:
        return "域名无法解析"
    for addr in ips:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _ip_verdict(ip):
            return _REJECT_INTERNAL
    return ""

async def fetch_page(url: str, *, max_chars: Optional[int] = None,
                     timeout: Optional[float] = None,
                     _client: Optional[httpx.AsyncClient] = None) -> dict:
    """抓一个网页并抽正文；**永不抛异常**。

    返回 {"ok","text","chars","reason","elapsed_ms"}。先过 _url_guard（SSRF 屏蔽：
    loopback/私有/链路本地/保留/非 http(s) → ok=False reason 且**不发起请求**），再
    httpx(trust_env=False) + 浏览器 UA + follow_redirects=True 取页面，抽正文后截断。
    """
    started = time.monotonic()

    def _done(ok: bool, text: str = "", reason: str = "") -> dict:
        return {"ok": bool(ok), "text": text, "chars": len(text), "reason": reason,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    target = str(url or "").strip()
    try:
        max_c = _page_max_chars() if max_chars is None else int(max_chars)
    except (TypeError, ValueError):
        max_c = DEFAULT_PAGE_MAX_CHARS
    max_c = max(0, min(MAX_PAGE_MAX_CHARS, max_c))
    try:
        to = _page_timeout() if timeout is None else float(timeout)
    except (TypeError, ValueError):
        to = DEFAULT_PAGE_TIMEOUT
    to = max(MIN_PAGE_TIMEOUT, min(MAX_PAGE_TIMEOUT, to))

    # 1) URL 守卫 —— 命中就返回，绝不发请求（SSRF 三例的 elapsed_ms 会是个位数）
    guard = await _url_guard(target)
    if guard:
        _log("抓取跳过（%s）：%s" % (guard, target[:90]))
        return _done(False, "", guard)

    # 2) 取页面
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    own_client = _client is None
    client = _client
    try:
        if own_client:
            client = httpx.AsyncClient(trust_env=False, follow_redirects=True,
                                       timeout=to, headers=headers)
        resp = await client.get(target, timeout=to)
    except Exception as exc:      # ConnectTimeout/DNS/TLS/TooManyRedirects…（取消不在 Exception 内）
        reason = type(exc).__name__ + ": " + (str(exc)[:100] or "无详情")
        _log("抓取失败（%s）：%s" % (reason, target[:90]))
        return _done(False, "", reason)
    finally:
        if own_client and client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    # 3) 状态码 / 类型闸
    if resp.status_code != 200:
        reason = "HTTP %d" % resp.status_code
        if resp.status_code in (401, 403, 429):
            reason += "（疑似反爬拦截）"
        _log("抓取跳过（%s）：%s" % (reason, target[:90]))
        return _done(False, "", reason)
    ctype = (resp.headers.get("content-type") or "").lower()
    if ctype and not any(k in ctype for k in ("html", "text", "xml")):
        reason = "非文本内容（%s）" % (ctype.split(";")[0].strip()[:40] or "?")
        _log("抓取跳过（%s）：%s" % (reason, target[:90]))
        return _done(False, "", reason)

    # 4) 抽正文
    try:
        text = _extract_text(_decode_html(resp.content, resp.text), max_c)
    except Exception as exc:
        return _done(False, "", "正文解析失败（%s）" % type(exc).__name__)
    if not text:
        _log("抓取跳过（正文为空）：%s" % target[:90])
        return _done(False, "", "正文为空")
    return _done(True, text, "")

async def enrich_sources(sources: list, *, limit: Optional[int] = None,
                         max_chars: Optional[int] = None,
                         timeout: Optional[float] = None) -> list:
    """并行抓前 limit 条结果的网页正文并写回 snippet；**永不抛异常**。

    - limit 默认取 config/search.json 的 fetch_pages（默认 3）；**limit<=0 → 原样返回**
      （不新增任何键，行为退回纯 snippet）。
    - 成功且正文非空 → 该条 snippet 替换为正文、page_ok=True、page_chars=N；
      **raw_snippet 永远保留原始搜索摘要**。失败/正文空 → 原样保留 snippet、page_ok=False。
    - asyncio.gather 并行 + asyncio.wait_for 总闸：超时用**已完成**的结果，其余保留原摘要。
    - 返回新列表（元素是浅拷贝），不改调用方传进来的对象。
    """
    items = list(sources) if isinstance(sources, list) else []
    if not items:
        return items
    try:
        n = _fetch_pages() if limit is None else int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_FETCH_PAGES
    n = min(n, len(items), MAX_RESULTS_CAP)
    if n <= 0:
        return items                              # 抓正文关闭 → 原样返回

    try:
        to = _page_timeout() if timeout is None else float(timeout)
    except (TypeError, ValueError):
        to = DEFAULT_PAGE_TIMEOUT
    to = max(MIN_PAGE_TIMEOUT, min(MAX_PAGE_TIMEOUT, to))
    gate = _page_total_timeout(to)

    started = time.monotonic()
    out = [dict(s) if isinstance(s, dict) else s for s in items]
    got: dict = {}                                # 下标 → fetch_page 结果（完成即写入）

    async def _one(idx: int, url: str, client: Optional[httpx.AsyncClient]) -> None:
        try:
            got[idx] = await fetch_page(url, max_chars=max_chars, timeout=to, _client=client)
        except Exception as exc:                  # fetch_page 自身不抛，这是最后一道保险
            got[idx] = {"ok": False, "text": "", "chars": 0, "elapsed_ms": 0,
                        "reason": "抓取异常（%s）" % type(exc).__name__}

    client: Optional[httpx.AsyncClient] = None
    try:
        client = httpx.AsyncClient(trust_env=False, follow_redirects=True, timeout=to,
                                   headers={"User-Agent": BROWSER_UA,
                                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    except Exception:
        client = None                             # 建不出共享连接池 → 每条自己建（fetch_page 兜底）

    timed_out = False
    try:
        await asyncio.wait_for(
            asyncio.gather(*[_one(i, str(out[i].get("url") or ""), client) for i in range(n)],
                           return_exceptions=True),
            timeout=gate)
    except asyncio.TimeoutError:
        timed_out = True                          # 总闸掐断：已完成的照用，其余保留原摘要
    except Exception as exc:
        _log("抓正文批次异常（%s: %s）" % (type(exc).__name__, str(exc)[:80]))
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    ok_n = 0
    for idx in range(n):
        src = out[idx]
        if not isinstance(src, dict):
            continue
        res = got.get(idx)
        if res and res.get("ok") and res.get("text"):
            src["snippet"] = res["text"]          # ← 正文写回 snippet（raw_snippet 不动）
            src["page_ok"] = True
            src["page_chars"] = int(res.get("chars") or len(res["text"]))
            ok_n += 1
        else:
            src["page_ok"] = False                # 失败/超时/空正文 → 原样保留搜索摘要
            src["page_chars"] = 0
    _log("抓正文 %d/%d 条成功 耗时=%dms%s"
         % (ok_n, n, int((time.monotonic() - started) * 1000), "（总闸超时）" if timed_out else ""))
    return out

async def _raw_search(q: str, n: int, time_range: str, *,
                      timeout: Optional[float] = None, label: str = "") -> dict:
    """**一次** bridge 往返 + 解析（search() 的最小调用单元）；**永不抛异常**。

    返回 {"ok","provider","cache","sources","content","reason","elapsed_ms"}；
    elapsed_ms 恒为**这一趟**的耗时。抓正文**不在这里**做 —— 质量闸可能弃用这一趟，
    在闸门之前抓正文就是白抓。
    label 仅用于日志前缀（"" = 首次，与改造前的日志逐字一致；"重试" = 质量闸重试）。
    """
    started = time.monotonic()
    payload: dict = {"query": q, "maxResults": n}
    tr = str(time_range or "").strip()
    if tr:
        payload["timeRange"] = tr
    url = _base_url() + "/raw-search"
    to = _timeout() if timeout is None else timeout

    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(url, json=payload, timeout=to)
    except Exception as exc:   # 连接拒绝/超时/DNS/TLS…（CancelledError 是 BaseException，不受影响）
        reason = type(exc).__name__ + ": " + (str(exc)[:120] or "无详情")
        _log(label + "跳过（" + reason + "）")
        return {"ok": False, "provider": "", "cache": "", "sources": [], "content": "",
                "reason": reason, "elapsed_ms": int((time.monotonic() - started) * 1000)}

    elapsed = int((time.monotonic() - started) * 1000)
    provider = cache = ""
    sources: list = []
    content = ""
    reason = ""

    if resp.status_code != 200:
        reason = "HTTP %d" % resp.status_code
    else:
        try:
            data = resp.json()
        except Exception as exc:
            data = None
            reason = "响应不是合法 JSON（%s）" % type(exc).__name__
        if not reason:
            if not isinstance(data, dict):
                reason = "响应结构异常（顶层不是对象）"
            elif data.get("ok") is not True:
                code = str(data.get("code") or "").strip() or "unknown"
                msg = str(data.get("message") or "").strip()
                reason = "bridge 拒绝（%s%s）" % (code, ("：" + msg) if msg else "")
            else:
                value = data.get("value")
                if not isinstance(value, dict):
                    reason = "响应缺少 value"
                else:
                    provider = str(value.get("provider") or "").strip()
                    cache = str(value.get("cache") or "").strip()
                    sources = _norm_sources(value.get("sources"))
                    cv = value.get("content")
                    content = "" if cv is None else str(cv)
                    if not sources:
                        reason = "无结果（sources 为空）"

    if reason:
        _log(label + "跳过（" + reason + "）")
        return {"ok": False, "provider": provider, "cache": cache, "sources": [],
                "content": "", "reason": reason, "elapsed_ms": elapsed}
    return {"ok": True, "provider": provider, "cache": cache, "sources": sources,
            "content": content, "reason": "", "elapsed_ms": elapsed}

async def search(query: str, max_results: int = 5, time_range: Optional[str] = None,
                 *, with_pages: bool = True) -> dict:
    """执行一次联网搜索；**永不抛异常**，失败 → ok=False + reason。

    with_pages=True（默认）→ 搜到结果后再并行抓前 N 条（config fetch_pages）的网页正文，
    把正文写回该条 snippet（抓不到就保留搜索摘要）；with_pages=False → 完全跳过抓正文。
    注意：snippet 的语义是「给模型看的最佳可用文本」，原始搜索摘要在 raw_snippet 里。

    time_range 为 None（调用方没给时间暗示）且首次结果相关度低于
    config quality_retry_min_relevance 时 → 自动用 time_range="month"（触发 exa）重试一次；
    详见模块 docstring「质量闸 + 一次 exa 重试」。
    """
    started = time.monotonic()
    q = str(query or "").strip()
    try:
        n = int(max_results)
    except (TypeError, ValueError):
        n = DEFAULT_MAX_RESULTS
    n = max(1, min(MAX_RESULTS_CAP, n))          # clamp 1..10
    if not q:
        _log("跳过（查询为空）")
        return _fail("查询为空")

    first_tr = str(time_range or "").strip()     # 调用方显式给的时间范围（给了就不重试）

    # ---- 第 1 趟 ----
    first = await _raw_search(q, n, first_tr)
    if not first["ok"]:
        return _fail(first["reason"], provider=first["provider"], cache=first["cache"],
                     elapsed_ms=first["elapsed_ms"])

    result = first
    elapsed_total = first["elapsed_ms"]          # 所有 bridge 往返之和
    first_provider = first["provider"]
    first_elapsed_ms = first["elapsed_ms"]
    retry_elapsed_ms = 0
    retried = False
    retry_reason = ""
    rel_first = _relevance(q, first["sources"])
    rel_final = rel_first

    # ---- 质量闸：只在"调用方没指定 time_range"且"首次结果不贴题"时重试一次 ----
    # 显式给了 time_range → 尊重调用方意图，绝不重试（否则等于偷偷改人家的检索条件）。
    if not first_tr:
        threshold = _quality_min_relevance()
        if rel_first < threshold:
            retry_to = max(MIN_TIMEOUT, min(_timeout(), QUALITY_RETRY_TIMEOUT))
            _log("质量闸：相关度=%.2f < %.2f → 用 exa 重试" % (rel_first, threshold))
            t_retry = time.monotonic()
            second: Optional[dict] = None
            try:
                # 双保险：httpx 自身超时（retry_to）+ asyncio.wait_for 外层硬闸（+0.5s 余量）。
                # 为什么不直接用主超时（15s）：重试是**补救**而不是主路径，让它有本事把
                # 回复链再拖 15s 不值得 —— 上限收到 QUALITY_RETRY_TIMEOUT（6 秒），
                # 实测 exa 重试约 1.2s，见 V1q_quality.md §4 耗时表。
                second = await asyncio.wait_for(
                    _raw_search(q, n, QUALITY_RETRY_TIME_RANGE,
                                timeout=retry_to, label="重试"),
                    timeout=retry_to + 0.5)
            except Exception as exc:      # 超时/取消/任何异常 → 静默降级，保留首次结果
                _log("重试异常（%s: %s）→ 保留首次结果"
                     % (type(exc).__name__, str(exc)[:80]))
            retry_elapsed_ms = int((time.monotonic() - t_retry) * 1000)
            elapsed_total += (second["elapsed_ms"] if isinstance(second, dict) else retry_elapsed_ms)

            if isinstance(second, dict) and second["ok"]:
                rel_second = _relevance(q, second["sources"])
                if rel_second > rel_first:
                    retried = True
                    result = second
                    rel_final = rel_second
                    retry_reason = ("相关度 %.2f < %.2f → 用 time_range=%s 重试成功"
                                    "（provider=%s）相关度 %.2f，采用重试结果"
                                    % (rel_first, threshold, QUALITY_RETRY_TIME_RANGE,
                                       second["provider"] or "?", rel_second))
                    _log("重试后 provider=%s 相关度=%.2f（采用）"
                         % (second["provider"] or "?", rel_second))
                else:
                    retry_reason = ("相关度 %.2f < %.2f → 用 time_range=%s 重试"
                                    "（provider=%s）相关度 %.2f 未更高，保留首次结果"
                                    % (rel_first, threshold, QUALITY_RETRY_TIME_RANGE,
                                       second["provider"] or "?", rel_second))
                    _log("重试后 provider=%s 相关度=%.2f（不采用）"
                         % (second["provider"] or "?", rel_second))
            else:
                why = (second["reason"] if isinstance(second, dict) else "异常/超时") or "?"
                retry_reason = ("相关度 %.2f < %.2f → 用 time_range=%s 重试失败（%s），"
                                "保留首次结果" % (rel_first, threshold,
                                                  QUALITY_RETRY_TIME_RANGE, why))
                _log("重试未成功（%s）→ 保留首次结果" % why)

    provider = result["provider"]
    sources = result["sources"]
    content = result["content"]
    cache = result["cache"]

    # ---- 抓正文（唯一的新增环节；失败一律优雅降级回搜索摘要，不影响 ok=True）----
    # **只对最终源集执行**：被质量闸弃用的首次结果一条也不抓（否则白花 3 次网页请求）。
    page_ms = 0
    pages_fetched = 0
    if with_pages:
        t_pages = time.monotonic()
        try:
            sources = await enrich_sources(sources)
        except Exception as exc:      # enrich_sources 契约也是"不抛"，双保险
            _log("抓正文跳过（%s: %s）" % (type(exc).__name__, str(exc)[:80]))
        page_ms = int((time.monotonic() - t_pages) * 1000)
        pages_fetched = sum(1 for s in sources if isinstance(s, dict) and s.get("page_ok"))

    _log("结果=%d 条 provider=%s 耗时=%dms" % (len(sources), provider or "?", elapsed_total))
    if with_pages:
        _log("正文=%d 条 抓取耗时=%dms" % (pages_fetched, page_ms))
    return {"ok": True, "provider": provider, "sources": sources, "content": content,
            "cache": cache, "reason": "", "elapsed_ms": elapsed_total,
            "page_elapsed_ms": page_ms, "pages_fetched": pages_fetched,
            # ---- 质量闸证据键（后加键，向后兼容）----
            "retried": retried, "retry_reason": retry_reason,
            "first_provider": first_provider, "relevance": rel_final,
            "first_relevance": rel_first, "first_elapsed_ms": first_elapsed_ms,
            "retry_elapsed_ms": retry_elapsed_ms,
            "wall_ms": int((time.monotonic() - started) * 1000)}

async def ping() -> tuple:
    """轻量探测 bridge 活不活：故意发一个空 query 的**非法**请求。

    bridge 会用 HTTP 200 + {"ok":false,"code":"search-rejected"} 拒绝它 —— 看到该应答
    即证明 bridge 在线，且**不消耗任何引擎配额/缓存**（不是一次真搜索）。
    base 不可达 / 超时 / 非 200 → (False, 原因)。
    """
    url = _base_url() + "/raw-search"
    timeout = max(MIN_TIMEOUT, min(5.0, _timeout()))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(url, json={"query": "", "maxResults": 1}, timeout=timeout)
    except Exception as exc:
        return False, "bridge 不可达（%s: %s）" % (type(exc).__name__, str(exc)[:120])
    if resp.status_code != 200:
        return False, "bridge 异常（HTTP %d）" % resp.status_code
    try:
        data = resp.json()
    except Exception:
        return True, "bridge 可达（响应非 JSON）"
    code = ""
    if isinstance(data, dict):
        code = str(data.get("code") or "").strip()
    return True, "bridge 可达（%s）" % (code or "ok")
