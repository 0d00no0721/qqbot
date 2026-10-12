#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Round 3 (收尾):
 A) 组合式"关思考"参数能否对全部思考型模型一次生效
 B) glm-5.3-flash 长输入在 max_tokens=4096 下的时延（对 20s/45s 预算的影响）
 C) mt=512 重复稳定性（思考型模型）
用法: $env:PROBE_KEY="<key>"; python probe_round3.py
"""
import json
import os
import time

import httpx

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8090/v1")
KEY = os.environ.get("PROBE_KEY", "")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "probe_results_round3.json")
TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0)
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
THINKING = ["qwen3.8-flash-next-2", "qwen3.8-flash-next", "glm-5.3-flash", "qwen3.8-reasoner"]


def call(model, messages, _key=None, **kw):
    payload = {"model": model, "messages": messages}
    payload.update(kw)
    t0 = time.perf_counter()
    rec = {}
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.post(f"{BASE}/chat/completions",
                       headers={"Authorization": f"Bearer {_key or KEY}",
                                "Content-Type": "application/json"}, json=payload)
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status"] = r.status_code
        rec["raw_head"] = r.text[:800]
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
    o = {"status": rec.get("status"), "elapsed_ms": rec.get("elapsed_ms"), "err": rec.get("err"),
         "finish_reason": None, "content": None, "content_len": None,
         "reasoning_content_len": None, "error_message": None, "raw_head": rec.get("raw_head")}
    b = rec.get("body")
    if isinstance(b, dict):
        ch = b.get("choices")
        if isinstance(ch, list) and ch and isinstance(ch[0], dict):
            o["finish_reason"] = ch[0].get("finish_reason")
            msg = ch[0].get("message") or {}
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
    res = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "combo": {}, "glm_long": [],
           "stability_mt512": {}}

    def save():
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    # A) 组合式关思考 (max_tokens=20)
    combos = [
        ("combo_ctk+re_none", {"chat_template_kwargs": {"enable_thinking": False},
                               "reasoning_effort": "none"}),
        ("combo_ctk+re_low", {"chat_template_kwargs": {"enable_thinking": False},
                              "reasoning_effort": "low"}),
        ("combo_ctk+re_none+thinking", {"chat_template_kwargs": {"enable_thinking": False},
                                        "reasoning_effort": "none",
                                        "thinking": {"type": "disabled"}}),
    ]
    for name, extra in combos:
        print(f"\n=== {name} (max_tokens=20) ===")
        rows = []
        for m in THINKING:
            e = extract(call(m, [{"role": "user", "content": BASIC}], max_tokens=20, **extra))
            e["model"] = m
            e["variant"] = name
            ok = bool((e["content"] or "").strip())
            print(f"  {m:<24} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
                  f"content={(e['content'] or '')[:20]!r} rc_len={e['reasoning_content_len']} "
                  f"-> {'WORKS' if ok else 'EMPTY'} "
                  f"{('ERR:' + str(e['error_message'])[:70]) if e['error_message'] else ''}")
            res["combo"].setdefault(name, []).append(e)
            save()

    # B) glm 长输入 max_tokens=4096
    print("\n=== glm-5.3-flash long input mx=4096 ===")
    for mt in [4096]:
        e = extract(call("glm-5.3-flash", [{"role": "user", "content": LONG_INPUT}], max_tokens=mt))
        e["max_tokens"] = mt
        res["glm_long"].append(e)
        print(f"  mt={mt} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
              f"clen={e['content_len']} rc_len={e['reasoning_content_len']} "
              f"content={(e['content'] or '')[:50]!r}")
        save()

    # C) mt=512 稳定性
    for m in THINKING:
        print(f"\n=== stability {m} mt=512 ===")
        rows = []
        for i in range(3):
            e = extract(call(m, [{"role": "user", "content": BASIC}], max_tokens=512))
            rows.append(e)
            print(f"  #{i+1} HTTP={e['status']} {e['elapsed_ms']}ms finish={e['finish_reason']} "
                  f"clen={e['content_len']} content={(e['content'] or '')[:20]!r}")
            res["stability_mt512"].setdefault(m, []).append(e)
            save()

    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save()
    print(f"\n[done] -> {OUT}")


if __name__ == "__main__":
    main()
