#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Round 2 探针:
 A) max_tokens 扫描 —— 找出"思考型"模型产出非空 content 的最低 max_tokens
 B) 关闭思考的尝试 (enable_thinking / reasoning_effort / thinking 等)
 C) 复核 round1 的可疑样本 (glm 长文, qwen3.8-flash-next response_format)
用法: $env:PROBE_KEY="<key>"; python probe_round2.py
"""
import json
import os
import time

import httpx

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8090/v1")
KEY = os.environ.get("PROBE_KEY", "")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe_results_round2.json")
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)

THINKING = [
    "qwen3.8-flash-next-2",
    "qwen3.8-flash-next",
    "glm-5.3-flash",
    "qwen3.8-reasoner",
    "smart/reasoning",
]
BASIC = "只回复两个字：正常"
LONG_INPUT = (
    "请阅读下面这段关于一个QQ机器人的技术说明，然后用一句话总结它最核心的风险点。\n\n"
    + ("这个QQ机器人运行在Windows上，通过OneBot11反向WebSocket接受NapCat的连接，"
       "内部包含决策引擎、记忆管理、用户画像、周礼、算卦等多个模块。它当前把模型调用分散在"
       "多个云端端点上，有的走中科大代理，有的走官方付费接口，密钥写在.env里由load_dotenv加载。"
       "子进程脚本model_primary和model_fallback不自己加载dotenv，而是继承父进程环境变量，"
       "所以直接手动运行它们会拿到空密钥。故障转移目前是两级熔断，主模型挂了才切备用模型。"
       "决策引擎用subprocess调用模型脚本，超时是60秒。配置里active_model.json只被读取model字段，"
       "而URL和key是硬编码在中科大代理上的。") * 12
)


def call(model, messages, _key=None, **kw):
    payload = {"model": model, "messages": messages}
    payload.update(kw)
    t0 = time.perf_counter()
    rec = {"model_sent": model, "extra_keys": [k for k in kw if k not in ("max_tokens",)]}
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.post(f"{BASE}/chat/completions",
                       headers={"Authorization": f"Bearer {_key or KEY}",
                                "Content-Type": "application/json"},
                       json=payload)
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status"] = r.status_code
        rec["raw_head"] = r.text[:1500]
        try:
            rec["body"] = r.json()
        except Exception:
            rec["body"] = None
    except Exception as e:
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status"] = None
        rec["err"] = f"{type(e).__name__}: {e}"
    return rec


def extract(rec):
    o = {"status": rec.get("status"), "elapsed_ms": rec.get("elapsed_ms"),
         "err": rec.get("err"), "finish_reason": None, "content": None,
         "content_len": None, "reasoning_content_len": None, "usage": None,
         "error_message": None, "raw_head": rec.get("raw_head")}
    b = rec.get("body")
    if isinstance(b, dict):
        o["usage"] = b.get("usage")
        ch = b.get("choices")
        if isinstance(ch, list) and ch and isinstance(ch[0], dict):
            c0 = ch[0]
            o["finish_reason"] = c0.get("finish_reason")
            msg = c0.get("message") or {}
            c = msg.get("content")
            o["content"] = c
            o["content_len"] = len(c) if isinstance(c, str) else None
            rc = msg.get("reasoning_content")
            o["reasoning_content_len"] = len(rc) if isinstance(rc, str) else None
        else:
            e = b.get("error")
            if isinstance(e, dict):
                o["error_message"] = e.get("message")
    return o


def main():
    res = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "sweep": {}, "variants": {}, "recheck": {}}

    def save():
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    # A) max_tokens 扫描
    for m in THINKING:
        rows = []
        print(f"\n=== sweep {m} ===")
        for mt in [20, 64, 128, 256, 512, 1024, 2048]:
            e = extract(call(m, [{"role": "user", "content": BASIC}], max_tokens=mt))
            e["max_tokens"] = mt
            rows.append(e)
            print(f"  mt={mt:<5} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
                  f"clen={e['content_len']} rc_len={e['reasoning_content_len']} content={(e['content'] or '')[:40]!r}")
            res["sweep"].setdefault(m, []).append(e)
            save()
        # 长输入在 mt=1024 / 2048 下
        for mt in [1024, 2048]:
            e = extract(call(m, [{"role": "user", "content": LONG_INPUT}], max_tokens=mt))
            e["max_tokens"] = mt
            e["case"] = "long_input"
            res["sweep"].setdefault(m, []).append(e)
            print(f"  LONG mt={mt:<5} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
                  f"clen={e['content_len']} rc_len={e['reasoning_content_len']} content={(e['content'] or '')[:40]!r}")
            save()

    # B) 关闭思考的尝试（max_tokens=20，只有 content 非空才算成功）
    variants = [
        ("baseline", {}),
        ("enable_thinking_false", {"enable_thinking": False}),
        ("chat_template_kwargs", {"chat_template_kwargs": {"enable_thinking": False}}),
        ("reasoning_effort_none", {"reasoning_effort": "none"}),
        ("reasoning_effort_low", {"reasoning_effort": "low"}),
        ("thinking_disabled", {"thinking": {"type": "disabled"}}),
        ("extra_body_ctk", {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}),
    ]
    for m in ["qwen3.8-flash-next-2", "glm-5.3-flash", "qwen3.8-reasoner"]:
        print(f"\n=== variants {m} (max_tokens=20) ===")
        rows = []
        for name, extra in variants:
            e = extract(call(m, [{"role": "user", "content": BASIC}], max_tokens=20, **extra))
            e["variant"] = name
            rows.append(e)
            flag = "WORKS" if (e["content"] or "").strip() else "empty"
            print(f"  {name:<24} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
                  f"content={(e['content'] or '')[:30]!r} rc_len={e['reasoning_content_len']} -> {flag} "
                  f"{('ERR:' + str(e['error_message'])[:80]) if e['error_message'] else ''}")
            res["variants"].setdefault(m, []).append(e)
            save()

    # C) 复核
    print("\n=== recheck ===")
    rc = {}
    rc["glm53_temp085_max500_recall"] = extract(call(
        "glm-5.3-flash", [{"role": "user", "content": "用一句话说明什么是递归。"}],
        temperature=0.85, max_tokens=500))
    rc["glm53_temp085_max2000"] = extract(call(
        "glm-5.3-flash", [{"role": "user", "content": "用一句话说明什么是递归。"}],
        temperature=0.85, max_tokens=2000))
    rc["qwen_next_response_format"] = extract(call(
        "qwen3.8-flash-next", [{"role": "user", "content": '只输出 JSON：{"ok":true}'}],
        max_tokens=300, response_format={"type": "json_object"}))
    rc["qwen_next_json_plain_mt300"] = extract(call(
        "qwen3.8-flash-next", [{"role": "user", "content": '只输出 JSON：{"ok":true}'}],
        max_tokens=300))
    for k, v in rc.items():
        print(f"  {k}: HTTP={v['status']} finish={v['finish_reason']} clen={v['content_len']} "
              f"rc_len={v['reasoning_content_len']} content={(v['content'] or '')[:80]!r} "
              f"{('ERR:' + str(v['error_message'])[:100]) if v['error_message'] else ''}")
    res["recheck"] = rc

    # D) 错误形态补充：非 chat 模型的错误码、超长 max_tokens
    print("\n=== extra error shapes ===")
    ex = {}
    ex["max_tokens_999999"] = extract(call("deepseek-flash", [{"role": "user", "content": "hi"}],
                                           max_tokens=999999))
    ex["bad_temperature"] = extract(call("deepseek-flash", [{"role": "user", "content": "hi"}],
                                         max_tokens=10, temperature="hot"))
    ex["ocr_model_chat"] = extract(call("unlimited-ocr", [{"role": "user", "content": "hi"}],
                                        max_tokens=10))
    ex["empty_messages"] = extract(call("deepseek-flash", [], max_tokens=10))
    for k, v in ex.items():
        print(f"  {k}: HTTP={v['status']} err={str(v['error_message'])[:120]!r} raw={(v['raw_head'] or '')[:200]}")
    res["extra_errors"] = ex

    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save()
    print(f"\n[done] -> {OUT}")


if __name__ == "__main__":
    main()
