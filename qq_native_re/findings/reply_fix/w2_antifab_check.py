#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W2 验证：路 B 只追加了那条禁编造约束（逐字），且路 A 未动。

对比对象（都是同一台机器上的真实快照，非人工抄写）：
  · pre-W1 备份 qq_native_re/patches/reply_fix_backup_20261012-103755/   ← 路 A 的最后一次合法形态
  · pre-W2 备份 qq_native_re/patches/reply_fix_backup_20261012-105922_antifab/  ← 本次改动前一刻
  · 当前 scripts/*.py

用法：cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w2_antifab_check.py
"""
import ast
import importlib.util
import os
import sys

ROOT = r"E:\QQbot"
PATCHES = os.path.join(ROOT, "qq_native_re", "patches")
BK_PRE_W1 = os.path.join(PATCHES, "reply_fix_backup_20261012-103755")
BK_PRE_W2 = os.path.join(PATCHES, "reply_fix_backup_20261012-105922_antifab")

sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

CTX = "[UC]: 摇一摇广告真烦\n[芽1为真]: 后台可以关允许摇晃感应"
TARGET = {"nickname": "UC", "uid": "1305489180", "content": "今日深圳天气"}
AVOID = ["（尾巴一甩）摇一摇广告这玩意儿是真缺德"]

# 本次唯一新增内容（逐字，含前导换行作为段落分隔）
SUFFIX = (
    "\n"
    "补充：若这条消息问的是实时/事实类信息（天气、新闻、比分、股价等），"
    "不要编造具体数值或细节（气温、涨跌幅、比分、日期都不要写具体数字）"
    "——可以说「本鱼查不到实时的」，然后自然地聊两句（比如「反正带伞总没错」）。"
)

FAILS = []


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def check(cond, what):
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}")
    if not cond:
        FAILS.append(what)


cur = {
    "primary": load(os.path.join(ROOT, "scripts", "model_primary.py"), "cur_primary"),
    "fallback": load(os.path.join(ROOT, "scripts", "model_fallback.py"), "cur_fallback"),
}
prev = {
    "primary": load(os.path.join(BK_PRE_W2, "model_primary.py"), "prev_primary"),
    "fallback": load(os.path.join(BK_PRE_W2, "model_fallback.py"), "prev_fallback"),
}


def legacy_path_a(path, ctx):
    """pre-W1 备份里还没有 _build_prompt，prompt 内联在 generate_reply。
    这里直接从源码 AST 取出那段字面量并求值（不手抄，避免抄错）。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "prompt" for t in node.targets)
                and isinstance(node.value, ast.JoinedStr)):
            return eval(compile(ast.Expression(node.value), "<bk>", "eval"), {"context": ctx})
    raise AssertionError(f"未在 {path} 找到 prompt 字面量")


print("===== 1) 路 A（无 target）与历史快照逐字一致 =====")
for fn in ("primary", "fallback"):
    c = cur[fn]._build_prompt(CTX, None, [])
    check(c == prev[fn]._build_prompt(CTX, None, []), f"{fn}: 与 pre-W2 备份逐字相同")
    check(c == legacy_path_a(os.path.join(BK_PRE_W1, "model_" + fn + ".py"), CTX),
          f"{fn}: 与 pre-W1 备份源码内联字面量逐字相同（AST 提取，非手抄）")
    check(c == cur[fn]._build_prompt(CTX, None, AVOID), f"{fn}: 无 target + avoid_recent 非空仍走同一路 A")
    check(cur[fn]._build_prompt(CTX, {}, []) == c, f"{fn}: 空 dict target 回落路 A")

print("===== 2) 路 B 相对 pre-W2 备份只多出指定后缀 =====")
for fn in ("primary", "fallback"):
    prev_no = prev[fn]._build_prompt(CTX, TARGET, [])
    prev_with = prev[fn]._build_prompt(CTX, TARGET, AVOID)
    check(prev_with.startswith(prev_no), f"{fn}: [前提] 旧版防复读段是追加在旧版路 B 之后")
    avoid_block = prev_with[len(prev_no):]          # 旧版防复读段原文（派生，不手抄）

    check(cur[fn]._build_prompt(CTX, TARGET, []) == prev_no + SUFFIX,
          f"{fn}[无 avoid_recent]: 新路 B == 旧路 B + 指定后缀（无其它字符变动）")
    # 有 avoid_recent 时：先补编造约束、再 avoid_recent
    check(cur[fn]._build_prompt(CTX, TARGET, AVOID) == prev_no + SUFFIX + avoid_block,
          f"{fn}[有 avoid_recent]: 新路 B == 旧路 B + 指定后缀 + 原防复读段（顺序：约束在前）")
    c = cur[fn]._build_prompt(CTX, TARGET, AVOID)
    check(c.index("补充：若这条消息问的是实时") < c.index("你最近在本群说过："),
          f"{fn}[有 avoid_recent]: 编造约束段确实在防复读段之前")
    check(avoid_block and avoid_block in c, f"{fn}[有 avoid_recent]: 防复读段逐字保留")

print("===== 3) 约束原文逐字存在 & 两文件一致 =====")
for fn in ("primary", "fallback"):
    p = cur[fn]._build_prompt(CTX, TARGET, AVOID)
    check("补充：若这条消息问的是实时/事实类信息" in p, f"{fn}: 含「实时/事实类信息」禁用条款")
    check("不要编造具体数值或细节（气温、涨跌幅、比分、日期都不要写具体数字）" in p, f"{fn}: 含具体数值禁用清单")
    check("——可以说「本鱼查不到实时的」，然后自然地聊两句" in p, f"{fn}: 含允许话术")
    check("请针对上面那条【你需要回应的最新消息】作答" in p, f"{fn}: 原有 4 条注意项仍在")

for avoid in ([], AVOID):
    check(cur["primary"]._build_prompt(CTX, TARGET, avoid)
          == cur["fallback"]._build_prompt(CTX, TARGET, avoid),
          f"primary 与 fallback 路 B 逐字一致（avoid_recent={'有' if avoid else '无'}）")

print()
print("总体:", "ALL PASS" if not FAILS else f"{len(FAILS)} 项 FAIL: {FAILS}")
sys.exit(0 if not FAILS else 1)
