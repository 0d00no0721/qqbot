#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 验证：无 target 时新 prompt 是否与改动前**逐字相同**。

用法：cd E:\QQbot && $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_prompt_baseline_check.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BK = os.path.join(ROOT, "qq_native_re", "patches", "reply_fix_backup_20261012-103755")

# 让被加载的 scripts/*.py 能找到 llm_pool（脚本直跑时 sys.path[0]=scripts/）
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

CTX = "[UC]: 摇一摇广告真烦\n[芽1为真]: 后台可以关允许摇晃感应"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def old_prompt(ctx):
    """改动前源码里的 prompt（逐字复制自备份文件）。"""
    return (
        f"以下是最近的群聊记录，请根据上下文自然地接话回复：\n\n{ctx}\n\n"
        "注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、"
        "不要使用 [昵称]: 的格式、不要包含 CQ 码。直接输出回复文本即可。"
    )


all_ok = True
for fn in ("model_primary.py", "model_fallback.py"):
    new = load(os.path.join(ROOT, "scripts", fn), "new_" + fn.replace(".py", ""))
    bk_src = open(os.path.join(BK, fn), encoding="utf-8").read()
    legacy = "以下是最近的群聊记录，请根据上下文自然地接话回复"

    checks = {
        "备份确实含旧 prompt 字面量": legacy in bk_src,
        "无 target 与旧版逐字相同": new._build_prompt(CTX, None, []) == old_prompt(CTX),
        "target=None+avoid_recent 非空仍走旧 prompt": (
            new._build_prompt(CTX, None, ["旧回复A", "旧回复B"]) == old_prompt(CTX)
        ),
        "空 dict target 回落旧 prompt": new._build_prompt(CTX, {}, []) == old_prompt(CTX),
        "content 全空白回落旧 prompt": (
            new._build_prompt(CTX, {"nickname": "UC", "content": "   "}, []) == old_prompt(CTX)
        ),
        "content 缺失回落旧 prompt": new._build_prompt(CTX, {"nickname": "UC"}, []) == old_prompt(CTX),
    }
    print(f"===== {fn} =====")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        all_ok = all_ok and v

    # 有 target：结构自检
    t = {"nickname": "UC", "uid": "10011", "content": "今日深圳天气"}
    p = new._build_prompt(CTX, t, ["（尾巴一甩）摇一摇广告这玩意儿是真缺德"])
    tchecks = {
        "含【你需要回应的最新消息】标题": "【你需要回应的最新消息】UC: 今日深圳天气" in p,
        "含背景隔离说明": "不要去回答背景里其它未被回应的问题" in p,
        "含针对本条作答指令": "请针对上面那条【你需要回应的最新消息】作答" in p,
        "含不确定就诚实说明": "就简短诚实地说明（不要编造）" in p,
        "含防复读段": "你最近在本群说过：" in p and "不要重复这些内容" in p,
        "含昵称清理约束": "不要使用 [昵称]: 的格式" in p,
        "背景原文仍在 prompt 内": CTX in p,
    }
    for k, v in tchecks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
        all_ok = all_ok and v

    # 无 avoid_recent 时不出现防复读段
    p2 = new._build_prompt(CTX, t, [])
    nb = "你最近在本群说过：" not in p2
    print(f"  [{'PASS' if nb else 'FAIL'}] avoid_recent 为空时不注入防复读段")
    all_ok = all_ok and nb

print()
print("总体:", "ALL PASS" if all_ok else "存在 FAIL")
sys.exit(0 if all_ok else 1)
