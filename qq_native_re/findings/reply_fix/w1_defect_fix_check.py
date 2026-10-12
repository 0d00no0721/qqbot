#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 缺陷修复验证：覆盖独立验证者提出的 D1 / D2 / D4。

D1 路由日志说谎：stderr 的 `定向回复=是/否` 必须与 _build_prompt 实际选路一致。
D2 CQ 码进 target：`[CQ:at,qq=BOT] ` 这类"纯 CQ + 空白"应回落旧 prompt，而不是
   把 `[CQ:at,…]` 塞进【你需要回应的最新消息】。
D4 _build_target 类型守卫：非 str message 不得抛异常；返回 None 时调用方不得崩。

用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_defect_fix_check.py
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

FAILS = []


def ck(cond, what, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILS.append(what)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


CTX = "小明: 你好\n小红: 在吗"
OLD = (f"以下是最近的群聊记录，请根据上下文自然地接话回复：\n\n{CTX}\n\n"
       "注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、"
       "不要使用 [昵称]: 的格式、不要包含 CQ 码。直接输出回复文本即可。")

print("=" * 76)
print("D2  decision_engine._build_target 的 CQ 码处理")
print("=" * 76)
from scripts.decision_engine import DecisionEngine as DE  # noqa: E402

cases_d2 = [
    ("纯正文", "今日深圳天气", "今日深圳天气"),
    ("@bot+正文", "[CQ:at,qq=2668851638] \n今日深圳天气", "今日深圳天气"),
    ("@bot+纯空白（验证者 D2 场景）", "[CQ:at,qq=2668851638] ", None),
    ("纯图片消息", "[CQ:image,file=a.png,url=https://x/y]", None),
    ("纯表情 CQ", "[CQ:face,id=4]", None),
    ("图片+文字", "[CQ:image,file=a.png] 看这个", "看这个"),
    ("reply+at+正文", "[CQ:reply,id=1][CQ:at,qq=2] 我不好截图", "我不好截图"),
    ("多个 CQ 混合", "[CQ:at,qq=1] [CQ:face,id=4] 你好 [CQ:image,file=b]", "你好"),
    ("普通方括号不受影响", "[公告] 明天放假", "[公告] 明天放假"),
]
for name, msg, want in cases_d2:
    got = DE._build_target(msg, "1", "小明")
    got_content = got["content"] if got else None
    ck(got_content == want, f"{name}", f"→ {got_content!r} (期望 {want!r})")

print()
print("=" * 76)
print("D4  _build_target 类型守卫")
print("=" * 76)
for bad in (None, 123, b"bytes", ["list"], {"k": "v"}, 4.5):
    try:
        r = DE._build_target(bad, "1", "小明")
        ck(r is None, f"message={type(bad).__name__} → None 不抛异常", f"→ {r!r}")
    except Exception as e:
        ck(False, f"message={type(bad).__name__} → None 不抛异常", f"抛了 {type(e).__name__}: {e}")
# 昵称/uid 异常类型也不能炸
for bad_nick in (None, 123, ["x"]):
    try:
        r = DE._build_target("正文", "1", bad_nick)
        ck(r is not None and r["nickname"] == "", f"sender_nickname={type(bad_nick).__name__} → 空串不抛",
           f"→ {r['nickname']!r}" if r else "→ None")
    except Exception as e:
        ck(False, f"sender_nickname={type(bad_nick).__name__} → 空串不抛", f"抛了 {type(e).__name__}")

print()
print("=" * 76)
print("D1  路由日志与 _build_prompt 实际选路一致（真子进程实测）")
print("=" * 76)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

import json  # noqa: E402

# 这些 content 形态都会让旧版日志说谎
d1_cases = [
    ("content='   '（纯空白）", {"nickname": "小明", "uid": "1", "content": "   "}),
    ("content='\\n\\t '（空白符）", {"nickname": "小明", "uid": "1", "content": "\n\t "}),
    ("content=''（空串）", {"nickname": "小明", "uid": "1", "content": ""}),
    ("content=正常文本", {"nickname": "小明", "uid": "1", "content": "今日深圳天气"}),
    ("target 缺失", None),
]
for script, tag in (("scripts/model_primary.py", "model_primary"),
                    ("scripts/model_fallback.py", "model_fallback")):
    mod = load(os.path.join(ROOT, script), "_d1_" + tag)
    print(f"  --- {tag} ---")
    for name, tgt in d1_cases:
        payload = {"context": CTX, "persona": "人设", "max_tokens": 8,
                   "api_base_url": "vstub://offline", "api_key": "k"}
        if tgt is not None:
            payload["target"] = tgt
        # 用离线桩避免真调模型：注入一个假 llm_pool
        code = (
            "import sys, types, json\n"
            "m = types.ModuleType('llm_pool')\n"
            "class E(Exception): pass\n"
            "async def chat(messages, **kw):\n"
            "    sys.stderr.write('CAPTURED_PROMPT_IS_OLD=' + str(messages[1]['content'].startswith('以下是最')) + '\\n')\n"
            "    return 'x', 'stub'\n"
            "m.chat = chat; m.LLMPoolExhausted = E\n"
            "sys.modules['llm_pool'] = m\n"
            "sys.modules['scripts.llm_pool'] = m\n"
            f"exec(open(r'{os.path.join(ROOT, script)}', encoding='utf-8').read())\n"
        )
        p = subprocess.run([sys.executable, "-c", code],
                           input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=ROOT, env=os.environ.copy())
        err = p.stderr.decode("utf-8", errors="replace")
        said_new = "定向回复=是" in err
        if "CAPTURED_PROMPT_IS_OLD=" in err:
            really_old = "CAPTURED_PROMPT_IS_OLD=True" in err
        else:
            really_old = None
        # 日志说"是" ⇒ 必须真的走了新 prompt（即 really_old == False）
        consistent = (said_new != really_old) if really_old is not None else None
        ck(consistent is True, f"{name}: 日志=是?{said_new} 实际走旧prompt?{really_old} 一致",
           err.strip().replace("\n", " | ")[:120])

print()
print("=" * 76)
print("回归：无 target 时 prompt 仍与改动前逐字相同（D1/D2 修复未破坏基线）")
print("=" * 76)
for script, tag in (("scripts/model_primary.py", "primary"), ("scripts/model_fallback.py", "fallback")):
    mod = load(os.path.join(ROOT, script), "_base_" + tag)
    ck(mod._build_prompt(CTX, None, []) == OLD, f"{tag}: 无 target 逐字相同")
    ck(mod._build_prompt(CTX, {"nickname": "A", "content": "  "}, []) == OLD, f"{tag}: 空白 content 回落")
    ck(mod._build_prompt(CTX, "非dict", []) == OLD, f"{tag}: 非 dict target 回落")
    # D1 的辅助函数与 _build_prompt 同源
    ck(mod._target_content({"content": "  "}) == "", f"{tag}: _target_content 空白→空")
    ck(mod._target_content("非dict") == "", f"{tag}: _target_content 非dict→空")
    ck(mod._target_content({"content": "x"}) == "x", f"{tag}: _target_content 正常透传")
    ck(mod._injected_recent(["a", "  ", None, "b"]) == ["a", "b"], f"{tag}: _injected_recent 过滤空/None")

print()
print("总体:", "ALL PASS" if not FAILS else f"{len(FAILS)} 项 FAIL")
for f in FAILS:
    print("  FAIL:", f)
sys.exit(0 if not FAILS else 1)
