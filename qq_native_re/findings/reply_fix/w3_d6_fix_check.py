#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W3 / D6 验证套件：`_validate_content` 是否真正使用 `error_keywords`。

D6：`scripts/decision_engine.py::_validate_content()` 从 config 读了
`failover.content_checks.error_keywords` 却从未在判定中使用
→ 主模型的报错话术被判为"内容合法" → 直接发群且**不触发**备用脚本。

本脚本三层验证：
  1) `_validate_content` 构造用例矩阵（含输入 → 判定 + 理由）
  2) 端到端降档：真实 scripts/model_primary.py + model_fallback.py 走**假模型池**
     （_d6_e2e/scripts/llm_pool.py，零网络零费用），主脚本吐错误话术，
     断言调用序列出现第二次调用（备用脚本）且最终结果符合预期
  3) 既有边界行为不变（空/过短/过长/乱码/敏感词/正常闲聊）

用法：
  cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
  # 正向：应 ALL PASS、exit=0
  & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w3_d6_fix_check.py
  # 反向：--sabotage 用**内存里临时构造的坏配置**证明"语义契约"确实会翻红
  #（只读真配置，不写任何配置文件；exit=0 表示"坏配置被抓住了"）
  & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w3_d6_fix_check.py --sabotage

--- W4b 更新说明（2026-10-12）----------------------------------------------
W4 把 error_keywords 的宽词 `error` 收窄为具体短语（20 → 22 条，实际新增
`an error occurred` / `error occurred` / `traceback (most recent call last)`，
`internal error` / `server error` 本就在列）。由此本脚本原有两处**陈旧设计**：

  1) 4 条用例的预期写死了"改前宽 error"语义 → 收窄后应改为放行：
     纯 `error`、句中大写 `Error`、误伤观测 `terror` / `error bar`。
  2) 旧"缺陷启发式"按"错误话术用例被判有效的条数"做**计数推断** → 收窄后
     恒有 2 条被判有效 → 永远打印 D6 缺陷存在、恒 exit=2（**假警报**）。

W4b 改法：删除计数推断，改为**显式语义契约**——
  · 应拦截组（BLOCK）：24 条报错话术用例必须逐条判无效
  · 应放行组（ALLOW）：14 条"收窄后应放行 + 正常闲聊"用例必须逐条判有效
  · 每条必拦短语另有**关键词覆盖证明**（列出生效的关键词），证明拦截不依赖
    已被移除的宽词 `error`
  · `--sabotage` 反向验证：从内存坏配置里移除某条关键词，对应短语必须翻红，
    否则本套件就是"瞎的"
原有熔断 / e2e / 保真度 / 配置健壮性断言（25 条）**一条未删、逐字保留**；
另加 2 条只读完整性断言（config/decision_rules.json 与 scripts/decision_engine.py
在本轮运行前后字节不变）。

--- V1r 刷新说明（2026-10-13）-------------------------------------------------
W5 把 `failover.content_checks.max_length` **由 500 放宽到 5000**
（2026-10-12，见 qq_native_re/findings/reply_fix/W5_maxlen_5000.md），
但本套件"边界"组当时把预期写死成"600 字必须判无效" → 600 字现在**合法**
→ 恒报 1 项 FAIL（**过期预期，不是代码回归**）。

V1r 改法（只改测试，不动任何运行时文件）：把"过短/过长"边界改为
**按当前配置动态取值**——
  · `min_length - 1` 字 → 必须判无效（理由含"内容过短"）
  · `min_length`   字 → 必须判有效
  · `max_length - 1` 字 → 必须判有效
  · `max_length + 1` 字 → 必须判无效（理由含"内容过长"）
判定文案也按 `min_length/max_length` **现读值**拼写断言（不再写死 500）。
其余边界（空串/纯空白/换行制表/纯 emoji/乱码/敏感词）与应拦 24 / 应放行 14
分组**逐条未动**。
`--sabotage` 相应扩成双向反向证明：内存里把 `max_length` 调小(10) / 调大(10**6)
必须各自让边界用例翻红，否则本套件在长度语义上就是"瞎的"。
"""
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
E2E = os.path.join(HERE, "_d6_e2e")
TMP_DIR = os.path.join(E2E, "_tmp")
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

SABOTAGE = "--sabotage" in sys.argv[1:]

RESULTS = {"cases": [], "e2e": [], "checks": [], "notes": []}
FAILS = []


def ck(cond, what, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}" + (f"   {extra}" if extra else ""))
    RESULTS["checks"].append({"name": what, "pass": bool(cond), "extra": extra})
    if not cond:
        FAILS.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# 运行前后字节不变 ⇒ 本套件只读，未改任何运行时文件（红线自证）
RUNTIME_READONLY = {
    "config/decision_rules.json": os.path.join(ROOT, "config", "decision_rules.json"),
    "scripts/decision_engine.py": os.path.join(ROOT, "scripts", "decision_engine.py"),
}
HASH_BEFORE = {k: sha256_file(p) for k, p in RUNTIME_READONLY.items()}


def evaluate_cases(engine, cases):
    """逐例跑 `_validate_content`，返回结果行（不打印、不断言）。

    `--sabotage` 会用同一个函数对"坏配置引擎"再跑一遍，保证对照口径一致。
    """
    rows = []
    for name, text, exp_valid, group in cases:
        valid, reason = engine._validate_content(text)
        rows.append({
            "name": name,
            "input": text[:80],
            "len": len(text),
            "valid": valid,
            "reason": reason,
            "expect_valid": exp_valid,
            "group": group,
        })
    return rows


# --------------------------------------------------------------------------
# 载入被测模块
# --------------------------------------------------------------------------
import scripts.decision_engine as demo  # noqa: E402
from scripts.decision_engine import DecisionEngine as DE, _SENSITIVE_KEYWORDS  # noqa: E402

_eng = DE()  # 真配置：config/decision_rules.json
_KWS = list(_eng._content_checks.get("error_keywords", []))

print("=" * 78)
print("D6  error_keywords 实际清单（来自 config/decision_rules.json，只读）")
print("=" * 78)
print(f"  条数 = {len(_KWS)}")
for i, k in enumerate(_KWS, 1):
    print(f"  {i:>2}. {k!r}")
RESULTS["error_keywords"] = _KWS

# --------------------------------------------------------------------------
# 构造用例矩阵
# --------------------------------------------------------------------------
CN_FILLER = "今天天气不错，要不要一起出去走走？"
# 注：写死字数的 LONG_600 已由 V1r 删除 —— 长度边界改为按当前配置动态取值
#（max_length 曾于 2026-10-12 由 500 放宽到 5000，见 W5_maxlen_5000.md）

BLOCK = "拦截"   # 应拦截：报错话术必须判无效
ALLOW = "放行"   # 应放行：正常回复 / 收窄后不再误伤的用例必须判有效
EDGE = "边界"    # 既有边界行为：改动前后一致


def emoji_only():
    return "\U0001F600" * 6


def gibberish():
    # 私用区字符：既非 CJK 也非全角/ASCII 标点 → valid 比例远低于 0.6
    return "\ue000" * 20


def sensitive_hit():
    return f"我来说说{_SENSITIVE_KEYWORDS[0]}的事情"


def pad_to(n):
    """造一条**恰好 n 字**的自然语言文本（无敏感词/无错误话术/非乱码）。

    `_validate_content` 的判定顺序是 过短 → 过长 → 乱码 → 错误话术，
    故长度边界用例不会掉进乱码/关键词分支。
    """
    return (CN_FILLER * (n // len(CN_FILLER) + 1))[:n]


# --- V1r：长度边界的阈值现读自 config/decision_rules.json（缺省沿用引擎兜底值） ---
def _read_max_length():
    p = os.path.join(ROOT, "config", "decision_rules.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        v = cfg.get("failover", {}).get("content_checks", {}).get("max_length")
        return int(v)
    except Exception:
        # 读不到就退回引擎启动时快照的值（引擎兜底 500），保证套件不因 IO 失败而"跳过边界"
        return int(_eng._content_checks.get("max_length", 500))


MAX_LEN = _read_max_length()
MIN_LEN = int(_eng._content_checks.get("min_length", 3))

LONG_BOUND_NAME = f"边界: 过长({MAX_LEN - 1}字, max={MAX_LEN}-1) 应有效"
LONG_OVER_NAME = f"边界: 过长({MAX_LEN + 1}字, max={MAX_LEN}) 应无效"
SHORT_UNDER_NAME = f"边界: 过短({MIN_LEN - 1}字, min={MIN_LEN})"
SHORT_AT_NAME = f"边界: 恰好({MIN_LEN}字, min={MIN_LEN}) 应有效"
DYN_EDGE_NAMES = (SHORT_UNDER_NAME, SHORT_AT_NAME, LONG_BOUND_NAME, LONG_OVER_NAME)


CASES = [
    # --- 应拦截组：报错话术必须判无效（D6 恢复目标） ---
    ("错误话术: Internal error 前缀", "Internal error: something went wrong", False, BLOCK),
    ("错误话术: 小写 internal error", "internal error, please try again", False, BLOCK),
    ("错误话术: 中文夹杂", "抱歉，刚才 internal error 了", False, BLOCK),
    ("错误话术: server error 500", "Server error 500 - gateway timeout", False, BLOCK),
    ("错误话术: An error occurred 句式", "An error occurred while processing your request", False, BLOCK),
    ("错误话术: Python Traceback 首行",
     'Traceback (most recent call last):\n  File "bot.py", line 42, in main\n'
     'ZeroDivisionError: division by zero', False, BLOCK),
    ("错误话术: request failed", "request failed, please retry later", False, BLOCK),
    ("错误话术: bad request", "bad request: invalid parameter", False, BLOCK),
    ("错误话术: exception", "Traceback exception occurred", False, BLOCK),
    ("错误话术: sorry,", "Sorry, I cannot do that right now", False, BLOCK),
    ("错误话术: i cannot", "I cannot answer that question", False, BLOCK),
    ("错误话术: i am unable", "I am unable to respond", False, BLOCK),
    ("错误话术: as an ai", "As an AI language model, I must decline", False, BLOCK),
    ("错误话术: i'm sorry", "I'm sorry, something broke", False, BLOCK),
    ("错误话术: i apologize", "I apologize for the confusion", False, BLOCK),
    ("错误话术: unable to", "unable to process your request", False, BLOCK),
    ("错误话术: could not", "could not reach the upstream model", False, BLOCK),
    ("错误话术: 这个话题我不太了解", "这个话题我不太了解", False, BLOCK),
    ("错误话术: 我们聊点别的吧", "我们聊点别的吧", False, BLOCK),
    ("错误话术: 换个话题吧", "换个话题吧", False, BLOCK),
    ("错误话术: 这个话题我不太清楚", "这个话题我不太清楚", False, BLOCK),
    ("错误话术: 我不太了解这个", "我不太了解这个，换个问题？", False, BLOCK),
    ("错误话术: 全大写 INTERNAL ERROR", "INTERNAL ERROR", False, BLOCK),
    ("错误话术: 多个关键词连击", "ERROR: exception, request failed", False, BLOCK),
    # --- 应放行组（W4b）：宽词 error 已移除，这些以前被判无效，现在必须放行 ---
    ("放行: 纯 error（宽词已移除）", "error", True, ALLOW),
    ("放行: 句中 Error 大写（宽词已移除）", "刚才发生了一个 Error 呢", True, ALLOW),
    ("放行: 中文句内裸 error", "这个 error 挺奇怪", True, ALLOW),
    ("放行: ErrorBar 驼峰标识符", "ArkUI 里有个 ErrorBar 组件挺好用", True, ALLOW),
    ("误伤观测: terror 内含 error", "泰罗奥特曼的 terror 名场面真帅", True, ALLOW),
    ("误伤观测: 英文句子含 error 单词", "The inflation rate is my favorite error bar", True, ALLOW),
    # --- 应放行组：正常回复 ---
    ("正常: 中文闲聊", CN_FILLER, True, ALLOW),
    ("正常: 抱歉我现在无法回答", "抱歉，我现在无法回答", True, ALLOW),
    ("正常: 含英文正常句", "Nice weather today, let's go out", True, ALLOW),
    ("正常: 长文本(100字)", "群友们好呀，" * 10, True, ALLOW),
    ("正常: 恰好 min_length=3", "在的呀", True, ALLOW),
    ("正常: 含 URL 但无关键词", "看这个 https://example.com/a?b=c", True, ALLOW),
    ("正常: 纯数字", "12345", True, ALLOW),
    ("正常: 表情+文字", "\U0001F600 好耶", True, ALLOW),
    # --- 既有边界：改动前后必须完全一致 ---
    ("边界: 空串", "", False, EDGE),
    ("边界: 纯空格", "   ", False, EDGE),
    ("边界: 换行制表", "\n\t ", False, EDGE),
    # V1r：过短/过长改为**按当前配置**取值。
    #   max_length 曾于 2026-10-12 由 500 放宽到 5000
    #   （用户明确产品要求；证据 qq_native_re/findings/reply_fix/W5_maxlen_5000.md）
    #   → 旧的"600 字必须判无效"是**过期预期**，不是代码回归；此处不再写死字数。
    (SHORT_UNDER_NAME, pad_to(MIN_LEN - 1), False, EDGE),
    (SHORT_AT_NAME, pad_to(MIN_LEN), True, EDGE),
    (LONG_BOUND_NAME, pad_to(MAX_LEN - 1), True, EDGE),
    (LONG_OVER_NAME, pad_to(MAX_LEN + 1), False, EDGE),
    ("边界: 纯 emoji", emoji_only(), False, EDGE),
    ("边界: 乱码（私用区）", gibberish(), False, EDGE),
    ("边界: 敏感词（_content_filter 负责）", sensitive_hit(), True, EDGE),
]

# 必拦短语 → 覆盖它的**具体**关键词（用于关键词覆盖证明与 --sabotage 消融）
CONTRACT_BLOCK = [
    {"label": "Internal error: x",
     "text": "Internal error: something went wrong",
     "kws": ("internal error",)},
    {"label": "Server error",
     "text": "Server error 500 - gateway timeout",
     "kws": ("server error",)},
    {"label": "An error occurred …",
     "text": "An error occurred while processing your request",
     "kws": ("an error occurred", "error occurred")},
    {"label": "Traceback (most recent call last):",
     "text": 'Traceback (most recent call last):\n  File "bot.py", line 42, in main\n'
             'ZeroDivisionError: division by zero',
     "kws": ("traceback (most recent call last)",)},
]

print()
print("=" * 78)
print("1) `_validate_content` 构造用例矩阵")
print("=" * 78)
print(f"  {'用例':<38} {'判定':<6} {'理由'}")
print("  " + "-" * 74)
RESULTS["cases"] = evaluate_cases(_eng, CASES)
for c in RESULTS["cases"]:
    valid = c["valid"]
    mark = "见下" if (valid != c["expect_valid"]) else ""
    print(f"  {c['name']:<38} {'有效' if valid else '无效':<6} {c['reason']}  {mark}")

# 判定矩阵断言（改动后应全部成立）
for c in RESULTS["cases"]:
    if c["valid"] != c["expect_valid"]:
        FAILS.append(f"用例判定不符: {c['name']} → valid={c['valid']} reason={c['reason']}")
        RESULTS["checks"].append({
            "name": f"用例判定: {c['name']}", "pass": False,
            "extra": f"valid={c['valid']} reason={c['reason']}",
        })
    else:
        RESULTS["checks"].append({
            "name": f"用例判定: {c['name']}", "pass": True,
            "extra": f"valid={c['valid']} reason={c['reason']}",
        })

# --------------------------------------------------------------------------
# W4b：显式语义契约（替代旧"错误话术用例被判有效的条数"计数推断）
# --------------------------------------------------------------------------
print()
print("  >>> W4b 语义契约（显式用例逐条断言，不做计数推断）")
block_rows = [c for c in RESULTS["cases"] if c["group"] == BLOCK]
allow_rows = [c for c in RESULTS["cases"] if c["group"] == ALLOW]
edge_rows = [c for c in RESULTS["cases"] if c["group"] == EDGE]
block_missed = [c for c in block_rows if c["valid"]]
allow_hurt = [c for c in allow_rows if not c["valid"]]
print(f"      应拦截组 {len(block_rows)} 例 → 判无效 {len(block_rows) - len(block_missed)} 例"
      + (f"，**漏放 {len(block_missed)} 例**: {[c['name'] for c in block_missed]}"
         if block_missed else "  ✓ 全拦"))
print(f"      应放行组 {len(allow_rows)} 例 → 判有效 {len(allow_rows) - len(allow_hurt)} 例"
      + (f"，**误伤 {len(allow_hurt)} 例**: {[c['name'] for c in allow_hurt]}"
         if allow_hurt else "  ✓ 全放"))
print(f"      既有边界 {len(edge_rows)} 例（判定见上表，已逐例断言，改动前后一致）")
ck(not block_missed, f"语义契约·应拦截组 {len(block_rows)} 例全部判无效（漏放 0 例）",
   f"漏放={[c['name'] for c in block_missed]}")
ck(not allow_hurt, f"语义契约·应放行组 {len(allow_rows)} 例全部判有效（误伤 0 例）",
   f"误伤={[c['name'] for c in allow_hurt]}")

# V1r：长度边界按**当前配置**断言，且理由文案必须点出正确的那一侧
#（max_length 曾于 2026-10-12 由 500 放宽到 5000；此处不写死 500/5000）
_by_name = {c["name"]: c for c in RESULTS["cases"]}
_len_bounds = (
    (SHORT_UNDER_NAME, "过短", False),
    (SHORT_AT_NAME, "有效", True),
    (LONG_BOUND_NAME, "有效", True),
    (LONG_OVER_NAME, "过长", False),
)
print()
print(f"  >>> V1r 长度边界（现读 config: min_length={MIN_LEN} / max_length={MAX_LEN}"
      f"；max_length 曾于 2026-10-12 由 500 放宽到 5000）")
for _n, _want_kind, _want_valid in _len_bounds:
    _r = _by_name.get(_n)
    if _r is None:
        ck(False, f"长度边界用例缺失: {_n}", "")
        continue
    _why = _r["reason"] or ""
    if _want_valid:
        _ok = (_r["valid"] is True)
    else:
        _ok = (_r["valid"] is False) and (_want_kind in _why)
    ck(_ok, f"V1r 长度边界·{_want_kind}: {_n[:24]}… → valid={_r['valid']}",
       f"len={_r['len']} reason={_why!r}")

# 关键词覆盖证明：每条必拦短语都由**具体关键词**命中，不依赖已被移除的宽词 error
for _item in CONTRACT_BLOCK:
    _hit = _eng._match_error_keyword(_item["text"], _KWS)
    ck(_hit in _item["kws"],
       f"覆盖证明: {_item['label']} 由 {list(_item['kws'])} 中之一命中",
       f"→ 实际命中 {_hit!r}")
ck("error" not in [k.lower() for k in _KWS],
   "宽词 'error' 已从 error_keywords 移除（本套件测的是收窄后语义）",
   f"当前 {len(_KWS)} 条")

defect_present = bool(block_missed or allow_hurt)
if defect_present:
    print("  >>> 结论：**语义契约被破坏**（存在漏放或误伤 → 真缺陷，必须修复）")
else:
    print("  >>> 结论：**D6 已修复**（error_keywords 生效：应拦截全拦、应放行全放）")
RESULTS["defect_present"] = defect_present
RESULTS["contract"] = {
    "block_total": len(block_rows),
    "block_missed": [c["name"] for c in block_missed],
    "allow_total": len(allow_rows),
    "allow_hurt": [c["name"] for c in allow_hurt],
    "edge_total": len(edge_rows),
    "block_phrases": [{"label": i["label"], "kws": list(i["kws"])} for i in CONTRACT_BLOCK],
}

# --------------------------------------------------------------------------
# S) 反向验证 --sabotage：内存坏配置必须让语义契约翻红（不写任何配置文件）
# --------------------------------------------------------------------------
def _engine_with_dropped_keywords(drop_lower):
    """构造"坏配置"引擎：从**内存副本**里移除指定关键词。

    只改 `eng._content_checks`（DE() 从 config 读入的内存 dict），
    绝不触碰 config/decision_rules.json（运行前后 SHA256 逐条核对）。
    """
    eng = DE()
    eng._content_checks = copy.deepcopy(eng._content_checks)
    kws = list(eng._content_checks.get("error_keywords", []))
    eng._content_checks["error_keywords"] = [k for k in kws if k.lower() not in drop_lower]
    return eng, kws


def _engine_with_max_length(new_max):
    """构造"坏配置"引擎：只把**内存里**的 max_length 换掉（不写配置文件）。

    V1r 反向证明用：长度语义被改坏时，边界用例必须翻红。
    """
    eng = DE()
    eng._content_checks = copy.deepcopy(eng._content_checks)
    eng._content_checks["max_length"] = new_max
    return eng


def _len_edge_diff(rows):
    """只取 V1r 动态长度边界 4 例中"判定与预期不符"的行。"""
    return [r for r in rows
            if r["group"] == EDGE and r["name"] in DYN_EDGE_NAMES
            and r["valid"] != r["expect_valid"]]


def _contract_diff(rows):
    return [r for r in rows
            if r["group"] in (BLOCK, ALLOW) and r["valid"] != r["expect_valid"]]


if SABOTAGE:
    print()
    print("=" * 78)
    print("S) 反向验证 --sabotage：坏配置必须让语义契约翻红（内存构造，不写配置文件）")
    print("=" * 78)
    print(f"  真配置 error_keywords = {len(_KWS)} 条（只读；运行前后 SHA256 核对见下）")

    # S-A 对照组：真配置 → 契约必须 PASS
    real_diff = _contract_diff(RESULTS["cases"])
    print()
    print(f"  对比 A｜真配置（{len(_KWS)} 条）: 契约用例 {len(block_rows) + len(allow_rows)} 例 → "
          f"{'PASS（0 例偏差）' if not real_diff else 'FAIL %d 例' % len(real_diff)}")
    ck(not real_diff, "S-A 对照：真配置下语义契约 PASS",
       f"偏差={[r['name'] for r in real_diff]}")

    # S-B 单点破坏：移除 'internal error'
    _DROP = "internal error"
    bad_eng, bad_kws = _engine_with_dropped_keywords({_DROP})
    bad_rows = evaluate_cases(bad_eng, CASES)
    bad_diff = _contract_diff(bad_rows)
    print()
    print(f"  对比 B｜坏配置（{len(bad_kws)} 条，仅移除 {_DROP!r}）: 契约用例 "
          f"{len(block_rows) + len(allow_rows)} 例 → "
          f"{'**PASS —— 套件是瞎的！**' if not bad_diff else 'FAIL %d 例' % len(bad_diff)}")
    for r in bad_diff:
        print(f"        [FAIL] {r['group']}｜{r['name']} → valid={r['valid']} "
              f"reason={r['reason']!r}（期望 valid={r['expect_valid']}）")
    ck(bool(bad_diff), f"S-B 移除 {_DROP!r} 后语义契约必须翻红（证明真会报警）",
       f"翻红 {len(bad_diff)} 例: {[r['name'] for r in bad_diff]}")
    ck(any(r["input"].lower().startswith("internal error") for r in bad_diff),
       "S-B 被抓住的正是 'Internal error' 用例（点名到具体用例）",
       f"命中={[r['name'] for r in bad_diff]}")

    # S-C 逐短语消融：移除某条必拦短语的覆盖关键词 → 该短语必须变为"放行"
    print()
    print("  逐短语消融（移除该短语的覆盖关键词后，它必须从'拦住'翻成'放行'）:")
    for _item in CONTRACT_BLOCK:
        _eng_i, _ = _engine_with_dropped_keywords({k.lower() for k in _item["kws"]})
        _rows_i = evaluate_cases(_eng_i, CASES)
        _tgt = [r for r in _rows_i if r["input"] == _item["text"][:80]]
        _flipped = bool(_tgt) and _tgt[0]["valid"] is True
        ck(_flipped,
           f"消融 {list(_item['kws'])} → {_item['label']} 必须变为放行（契约随之翻红）",
           f"valid={_tgt[0]['valid'] if _tgt else '用例缺失'}"
           f" reason={_tgt[0]['reason'] if _tgt else '-'}")

    # S-D / S-E 长度语义双向破坏（V1r）：内存里改 max_length 必须让边界用例翻红
    print()
    print("  V1r 长度边界消融（内存里改 max_length；真配置与 config 文件均不动）:")
    _len_diff_A = _len_edge_diff(RESULTS["cases"])
    ck(not _len_diff_A, f"S-D 对照：真配置（max_length={MAX_LEN}）下长度边界 4 例全部 PASS",
       f"偏差={[r['name'] for r in _len_diff_A]}")

    _SAB_SMALL = 10
    _len_eng_small = _engine_with_max_length(_SAB_SMALL)
    _len_rows_small = evaluate_cases(_len_eng_small, CASES)
    _len_diff_small = _len_edge_diff(_len_rows_small)
    print(f"        max_length {MAX_LEN} → {_SAB_SMALL}（内存）: 翻红 {len(_len_diff_small)} 例")
    for r in _len_diff_small:
        print(f"          [FAIL] {r['name']} → len={r['len']} valid={r['valid']} "
              f"reason={r['reason']!r}（期望 valid={r['expect_valid']}）")
    ck(bool(_len_diff_small),
       f"S-D 调小 max_length（{MAX_LEN}→{_SAB_SMALL}）长度边界必须翻红（证明真会报警）",
       f"翻红 {len(_len_diff_small)} 例: {[r['name'] for r in _len_diff_small]}")
    ck(any(r["name"] == LONG_BOUND_NAME for r in _len_diff_small),
       "S-D 被抓住的正是【本该合法却被判过长】的那条（点名到具体用例）",
       f"命中={[r['name'] for r in _len_diff_small]}")

    _SAB_LARGE = 10 ** 6
    _len_eng_large = _engine_with_max_length(_SAB_LARGE)
    _len_rows_large = evaluate_cases(_len_eng_large, CASES)
    _len_diff_large = _len_edge_diff(_len_rows_large)
    print(f"        max_length {MAX_LEN} → {_SAB_LARGE}（内存）: 翻红 {len(_len_diff_large)} 例")
    for r in _len_diff_large:
        print(f"          [FAIL] {r['name']} → len={r['len']} valid={r['valid']} "
              f"reason={r['reason']!r}（期望 valid={r['expect_valid']}）")
    ck(bool(_len_diff_large),
       f"S-E 调大 max_length（{MAX_LEN}→{_SAB_LARGE}）长度边界必须翻红（反方向也会报警）",
       f"翻红 {len(_len_diff_large)} 例: {[r['name'] for r in _len_diff_large]}")
    ck(any(r["name"] == LONG_OVER_NAME and r["valid"] is True for r in _len_rows_large),
       "S-E 被抓住的正是【超长却被放行】的那条（长度上限形同虚设）",
       f"命中={[r['name'] for r in _len_diff_large]}")

    # 只读自证：sabotage 路径同样不得改写任何运行时文件
    print()
    for _k, _p in RUNTIME_READONLY.items():
        ck(HASH_BEFORE[_k] == sha256_file(_p),
           f"S 只读完整性: {_k} 未被本套件改写", f"sha256={HASH_BEFORE[_k][:12]}…")

    RESULTS["sabotage"] = {
        "mode": True,
        "real_config_kw_count": len(_KWS),
        "compare_A_real": {"pass": not real_diff,
                           "diff": [r["name"] for r in real_diff]},
        "compare_B_bad": {"dropped": _DROP, "kw_count": len(bad_kws),
                          "diff": [{"name": r["name"], "valid": r["valid"],
                                    "reason": r["reason"]} for r in bad_diff]},
        "ablation": [{"kws": list(i["kws"]), "label": i["label"]}
                     for i in CONTRACT_BLOCK],
        "readonly_hashes": dict(HASH_BEFORE),
    }

    out_sab = os.path.join(HERE, "w3_d6_results_sabotage.json")
    with open(out_sab, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print()
    print("=" * 78)
    if FAILS:
        print(f"反向验证结论: **未达预期**（{len(FAILS)} 项不符）"
              f"—— 坏配置没被抓住，本套件不可信")
        for f_ in FAILS:
            print("  FAIL:", f_)
        print(f"结果已写盘: {out_sab}")
        sys.exit(3)
    print("反向验证结论: **契约具备真报警能力**"
          "（真配置 PASS / 坏配置 FAIL 且点名到具体用例）")
    print(f"结果已写盘: {out_sab}")
    sys.exit(0)

# --------------------------------------------------------------------------
# 2) 端到端降档（真子进程 + 假模型池）
# --------------------------------------------------------------------------
REAL_PRIMARY = os.path.join(ROOT, "scripts", "model_primary.py")
REAL_FALLBACK = os.path.join(ROOT, "scripts", "model_fallback.py")
STUB_PRIMARY = os.path.join(E2E, "scripts", "model_primary_stub.py")
STUB_FALLBACK = os.path.join(E2E, "scripts", "model_fallback_stub.py")

import asyncio  # noqa: E402

_ENGINE_LOGS = []


class _Cap:
    def write(self, s):
        _ENGINE_LOGS.append(s)

    def flush(self):
        pass


# --------------------------------------------------------------------------
# 沙箱适配（只影响本测试，不影响产品代码）：
#   本机 DSH 沙箱下 `asyncio.create_subprocess_exec` 抛 WinError 5，
#   且"子进程继承父进程管道句柄"会让子进程卡在启动阶段。
#   因此 `_sync_call` 是引擎 `_call_model_script()` 的**逐行等价复刻**：
#   同一份 stdin payload、同一套 stderr 日志、同样的 rc/JSON 错误处理、
#   同样的 `(result.get("reply") or "").strip()`；
#   唯一的差别是"起进程 + 管道"这一层改成：同步 subprocess.run + 文件重定向。
#   下面的保真度对比项会证明它与引擎原实现逐项一致。
# --------------------------------------------------------------------------
def _sync_call(engine, script_path, gid, max_tokens=500, api_key="", target=None,
               prefetch=None):
    demo.logger.info("[调试] _call_model_script 被调用: script=%s, gid=%s",
                     os.path.basename(script_path), gid)
    if target:
        demo.logger.info("[定向回复] 目标: %s(%s) | 内容: %s",
                         target.get("nickname") or "?", target.get("uid") or "?",
                         (target.get("content") or "")[:80])
    else:
        demo.logger.info("[定向回复] 本轮无 target（沿用旧 prompt）")

    if prefetch is None:
        context_text = asyncio.run(engine._format_context(gid))
        system_prompt = engine._load_character(gid)
        active_model = engine.get_active_model()
    else:
        context_text, system_prompt, active_model = prefetch
    effective_key = api_key if api_key else active_model["api_key"]
    avoid_recent = [r for r in engine._recent_bot_replies.get(gid, ()) if r]

    input_data = json.dumps({
        "context": context_text,
        "persona": system_prompt,
        "max_tokens": max_tokens,
        "model": active_model["model"],
        "preferred_model": active_model["model"],
        "api_base_url": active_model["api_base_url"],
        "api_key": effective_key,
        "target": target,
        "avoid_recent": avoid_recent,
    }, ensure_ascii=False)

    os.makedirs(TMP_DIR, exist_ok=True)
    tag = f"{os.path.basename(script_path)}.{abs(hash((script_path, gid, target and target.get('content')))) % 10**8}"
    ipath = os.path.join(TMP_DIR, tag + ".in.json")
    opath = os.path.join(TMP_DIR, tag + ".out.txt")
    epath = os.path.join(TMP_DIR, tag + ".err.txt")
    with open(ipath, "w", encoding="utf-8") as f:
        f.write(input_data)
    stdin_f = open(ipath, "rb")
    stdout_f = open(opath, "wb")
    stderr_f = open(epath, "wb")
    try:
        proc = subprocess.run(
            [sys.executable or "python", script_path],
            stdin=stdin_f, stdout=stdout_f, stderr=stderr_f, timeout=60.0,
        )
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        demo.logger.warning("模型脚本 %s 调用超时（>60s）", os.path.basename(script_path))
        return ""
    except Exception as e:
        demo.logger.error("模型脚本 %s 调用失败: %s", os.path.basename(script_path), e)
        return ""
    finally:
        stdin_f.close()
        stdout_f.close()
        stderr_f.close()

    raw_out = open(opath, "rb").read()
    raw_err = open(epath, "rb").read()

    if rc != 0:
        stderr_text = raw_err.decode("utf-8", errors="replace").strip()
        demo.logger.error("模型脚本 %s 异常退出 (code=%d): %s",
                          os.path.basename(script_path), rc, stderr_text[:200])
        return ""

    output = raw_out.decode("utf-8", errors="replace").strip()
    stderr_text = raw_err.decode("utf-8", errors="replace").strip()
    if stderr_text:
        demo.logger.error("模型脚本 %s stderr: %s",
                          os.path.basename(script_path), stderr_text[:512])
    if not output:
        return ""
    try:
        result = json.loads(output)
    except json.JSONDecodeError:
        demo.logger.error("模型脚本 %s 输出不是合法 JSON", os.path.basename(script_path))
        return ""
    return (result.get("reply") or "").strip()


def _prep_env(primary, fallback, qfile, cfile):
    env = os.environ.copy()
    env["_D6_PRIMARY_TARGET"] = primary
    env["_D6_FALLBACK_TARGET"] = fallback
    env["_D6_QUEUE_FILE"] = qfile
    env["_D6_CALLS_FILE"] = cfile
    for k in ("PYTHONPATH", "PYTHONSTARTUP"):
        env.pop(k, None)
    return env


class _AllHandlers:
    """把 logger 的所有 StreamHandler + sys.stderr 全部重定向到捕获器。"""

    def __enter__(self):
        import logging
        self.saved_streams = []
        for h in list(demo.logger.handlers) + list(demo.logger.parent.handlers):
            if isinstance(h, logging.StreamHandler):
                self.saved_streams.append((h, getattr(h, "stream", None)))
                h.stream = _Cap()
        self.saved_stderr = sys.stderr
        sys.stderr = _Cap()
        del _ENGINE_LOGS[:]
        return self

    def __exit__(self, *exc):
        sys.stderr = self.saved_stderr
        for h, st in self.saved_streams:
            h.stream = st
        return False


# --- 构造用例：error_keywords 配置异常时不得炸（返回 "" 放行） ---
print()
print("  --- 配置异常健壮性：_match_error_keyword ---")
for cfg in (None, [], "not-a-list", 123, [None, 123, "", "error"], ("exception",), {"a": 1}):
    try:
        r = DE._match_error_keyword("这是个 normal 回复 error", cfg)
        ok = isinstance(r, str)
        ck(ok, f"error_keywords={cfg!r} → 返回字符串不抛异常", f"→ {r!r}")
    except Exception as e:
        ck(False, f"error_keywords={cfg!r} → 返回字符串不抛异常",
           f"抛了 {type(e).__name__}: {e}")


def _engine_payload(engine, script_path, gid="99999", max_tokens=500, api_key="",
                    target=None):
    r"""用引擎**自己**的 _call_model_script 构出 stdin payload（原始实现，不改一行）。

    做法：临时把 `json.dumps` 换成间谍（引擎里 `import json` 后就是模块属性查找），
    `asyncio.create_subprocess_exec` 被本机沙箱拒绝（WinError 5）之前，
    引擎已经把 input_data 组装完并调用了 json.dumps —— 间谍截获的就是原始 payload。
    用它和 _sync_call 的 payload 做逐键对比 = 证明"喂给真脚本的输入完全一致"。
    """
    obs = {}
    eng = engine or DE()
    call = eng._call_model_script

    async def _probe(path, g, mt=500, k="", tgt=None):
        await call(path, g, mt, k, tgt)

    real_dumps = demo.json.dumps

    def _spy_dumps(obj, *a, **kw):
        if isinstance(obj, dict) and "context" in obj and "persona" in obj:
            obs["payload"] = obj
        return real_dumps(obj, *a, **kw)

    demo.json.dumps = _spy_dumps
    try:
        with _AllHandlers():
            asyncio.run(_probe(script_path, gid, max_tokens, api_key, target))
    finally:
        demo.json.dumps = real_dumps
    return obs.get("payload")


def _sync_payload(engine, script_path, gid="99999", max_tokens=500, api_key="",
                  target=None):
    """_sync_call 构出的 payload（把它的 subprocess.run 换成假进程来截获 stdin）。"""
    obs = {}
    real_run = subprocess.run

    class _FakeProc:
        returncode = 0

    def _fake_run(cmd, stdin=None, stdout=None, stderr=None, timeout=None, **kw):
        obs["payload"] = json.loads(stdin.read().decode("utf-8"))
        # 让"子进程"产出一份合法但空的 JSON，等价于脚本无回复
        stdout.write(b'{"reply": ""}\n')
        stdout.flush()
        return _FakeProc()

    subprocess.run = _fake_run
    try:
        with _AllHandlers():
            _sync_call(engine, script_path, gid, max_tokens, api_key, target)
    finally:
        subprocess.run = real_run
    return obs.get("payload")


def e2e(label, queue, expected, engine=None, record=True, target=None,
        use_sync=True, impl=None):
    """跑一轮 _generate_reply_with_failover，返回 (结果, 证据 dict)。

    engine=None → 真实引擎（子进程调用换成沙箱可用的同步等价实现 _sync_call）。
    record=False → 不注入记录包装（用于真实调用路径的保真度对比）。
    """
    qfile = os.path.join(E2E, f"_queue_{label}.json")
    cfile = os.path.join(E2E, f"_calls_{label}.jsonl")
    with open(qfile, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False)
    if os.path.exists(cfile):
        os.remove(cfile)

    eng = engine or DE()
    eng._primary_script = STUB_PRIMARY
    eng._fallback_script = STUB_FALLBACK

    # _sync_call 是同步实现（在 async 熔断函数里会被直接调用，不能嵌套 loop），
    # 所以它需要的三个协程结果在这里预先取好。
    prefetch = (
        asyncio.run(eng._format_context("99999")),
        eng._load_character("99999"),
        eng.get_active_model(),
    ) if record else None

    targets = []
    if record:
        async def _rec(script_path, gid, max_tokens=500, api_key="", target=None):
            targets.append((os.path.basename(script_path), target))
            if impl is not None:
                return impl(script_path, gid, max_tokens, api_key, target)
            return _sync_call(eng, script_path, gid, max_tokens, api_key, target,
                              prefetch=prefetch)
        eng._call_model_script = _rec  # 只做记录，判定逻辑仍走真实实现
        # 注意：_rec 是 async；下面若走 impl 分支，impl 必须也是 async（见 fidelity 段）

    env = _prep_env(REAL_PRIMARY, REAL_FALLBACK, qfile, cfile)
    old_env = dict(os.environ)
    for k in ("_D6_PRIMARY_TARGET", "_D6_FALLBACK_TARGET",
              "_D6_QUEUE_FILE", "_D6_CALLS_FILE"):
        os.environ.pop(k, None)
    os.environ.update({k: env[k] for k in env if k.startswith("_D6_")})

    with _AllHandlers():
        try:
            result = asyncio.run(eng._generate_reply_with_failover("99999", target=target))
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    stderr_log = "".join(_ENGINE_LOGS)
    calls = []
    if os.path.exists(cfile):
        with open(cfile, "r", encoding="utf-8") as f:
            calls = [json.loads(l) for l in f if l.strip()]

    ev = {
        "label": label,
        "queue": [q if len(q) < 60 else q[:57] + "..." for q in queue],
        "result": result,
        "expected": expected,
        "engine_log": stderr_log.strip(),
        "child_call_labels": [c.get("label") for c in calls],
        "child_prompts": [len(c.get("prompt", "")) for c in calls],
        "failover_marker": "调用备用模型" in stderr_log,
        "gave_up": any("放弃回复" in l for l in stderr_log.splitlines()),
        "targets_seen": [t for _, t in targets],
        "scripts_seen": [s for s, _ in targets],
    }
    RESULTS["e2e"].append(ev)
    return result, ev


print()
print("=" * 78)
print("2) 端到端降档：主脚本吐错误话术 → 必须真的再调备用脚本")
print("=" * 78)
E2E_PLAN = [
    ("primary_error_then_fallback_ok",
     ["Internal error: something went wrong", CN_FILLER],
     {"calls": 2, "result": CN_FILLER, "marker": True}),
    ("primary_error_then_fallback_gives_up",
     ["Internal error: something went wrong", "Internal error: fallback also broken"],
     {"calls": 2, "result": "", "marker": True}),
    ("both_normal_no_failover",
     [CN_FILLER],
     {"calls": 1, "result": CN_FILLER, "marker": False}),
    ("both_normal_fallback_used_on_empty",
     ["", CN_FILLER],
     {"calls": 2, "result": CN_FILLER, "marker": True}),
]
for label, queue, exp in E2E_PLAN:
    res, ev = e2e(label, queue, exp)
    n = len(ev["child_call_labels"])
    print(f"  --- {label} ---")
    print(f"      预置队列        : {ev['queue']}")
    print(f"      子进程调用序列  : {ev['child_call_labels']}  (共 {n} 次)")
    print(f"      最终返回        : {res!r}")
    print(f"      引擎熔断日志    : {ev['engine_log'].replace(chr(10), ' | ')}")
    if exp["calls"] == 2:
        ck(n >= 2 and ev["child_call_labels"][0] == "model_primary"
           and ev["child_call_labels"][1] == "model_fallback",
           f"{label}: 主→备 第二次调用已发生",
           f"序列={ev['child_call_labels']}")
    else:
        ck(n == 1, f"{label}: 未触发降档（只调 1 次）", f"序列={ev['child_call_labels']}")
    ck(res == exp["result"], f"{label}: 最终结果符合预期", f"{res!r} == {exp['result']!r}")
    ck(ev["failover_marker"] is exp["marker"],
       f"{label}: 熔断标记 {'出现' if exp['marker'] else '不出现'}")

# --- 保真度：_sync_call（沙箱适配） vs 引擎原始构造 ---
print("  --- 保真度对比：_sync_call（沙箱适配层） vs 引擎原始实现 ---")
ENG = DE()
FIDELITY = [
    ("fidelity_no_target", None),
    ("fidelity_with_target", {"nickname": "小明", "uid": "10001", "content": "bot 你看这个"}),
]
for flabel, ftgt in FIDELITY:
    p_engine = _engine_payload(ENG, REAL_PRIMARY, target=ftgt)
    p_sync = _sync_payload(ENG, REAL_PRIMARY, target=ftgt)
    shown = json.dumps(p_sync, ensure_ascii=False)[:130] if p_sync else "(None)"
    ck(p_engine is not None and p_sync is not None and p_engine == p_sync,
       f"{flabel}: stdin payload 与引擎原实现逐键完全一致",
       f"引擎 payload={'有' if p_engine else '无'} | 同步 payload={shown}")

# --- 保真度 2：给真脚本的输入/输出通路一致（同一份真脚本 + 同一个假模型池） ---
FIDELITY2 = [
    ("fidelity_primary_error", ["Internal error: bogus", CN_FILLER]),
    ("fidelity_primary_ok", [CN_FILLER]),
    ("fidelity_both_empty", ["", ""]),
]
for flabel, fqueue in FIDELITY2:
    r1, ev1 = e2e(flabel + "_a", fqueue, {}, engine=DE(), record=True)
    r2, ev2 = e2e(flabel + "_b", fqueue, {}, engine=DE(), record=True)
    ck(r1 == r2 and ev1["child_call_labels"] == ev2["child_call_labels"]
       and ev1["child_prompts"] == ev2["child_prompts"],
       f"{flabel}: 重复两轮结果稳定（真脚本 + 假模型池可复现）",
       f"返回 {r1!r}=={r2!r} | 序列 {ev1['child_call_labels']} | prompt {ev1['child_prompts']}")

# target 不漂移：单独一轮，显式传 target
print("  --- target_shape_check（显式 target，断言主备收到同一个） ---")
tgt = {"nickname": "小明", "uid": "10001", "content": "bot 你看这个"}
res, ev = e2e("target_shape_check",
              ["Internal error: boom", CN_FILLER],
              {"calls": 2, "result": CN_FILLER, "marker": True}, target=tgt)
same = (len(ev["targets_seen"]) == 2 and ev["targets_seen"][0] == tgt
        and ev["targets_seen"][1] == tgt)
ck(same, "降档后 target 未漂移（主备同一 target）", f"{ev['targets_seen']}")

# --- 只读完整性：本套件运行前后，运行时文件字节不变 ---
print("  --- 只读完整性：本套件运行前后，运行时文件字节不变 ---")
for _k, _p in RUNTIME_READONLY.items():
    ck(HASH_BEFORE[_k] == sha256_file(_p),
       f"{_k}: 运行前后 SHA256 不变（本套件只读，未改运行时文件）",
       f"sha256={HASH_BEFORE[_k][:12]}…")
RESULTS["readonly_hashes"] = dict(HASH_BEFORE)

# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
RESULTS["notes"] = [
    "W4b: 旧'缺陷启发式'（按错误话术用例被判有效的条数计数）已删除，改为显式语义契约",
    f"契约分组: 应拦截 {len(block_rows)} 例 / 应放行 {len(allow_rows)} 例 / 既有边界 {len(edge_rows)} 例",
    "反向验证: 另跑 --sabotage（内存坏配置，不写配置文件）",
]
out = os.path.join(HERE, "w3_d6_results.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
print()
print("=" * 78)
if defect_present:
    print("总体: 语义契约被破坏（漏放/误伤）—— D6 缺陷或误伤回归，需要修复（见下方 FAIL）")
print(f"总体: {'ALL PASS' if not FAILS else str(len(FAILS)) + ' 项 FAIL'}")
for f_ in FAILS:
    print("  FAIL:", f_)
print(f"结果已写盘: {out}")
sys.exit(0 if not FAILS else 1)
