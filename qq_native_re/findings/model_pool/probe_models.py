#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地网关模型能力探针 (127.0.0.1:8090/v1)
用法:  $env:PROBE_KEY="<key>"; python probe_models.py
输出:  probe_results.json (原始) + stdout 摘要
"""
import json
import os
import sys
import time
import traceback

import httpx

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:8090/v1")
KEY = os.environ.get("PROBE_KEY", "")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_results.json")

TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

TIER = {
    "deepseek-flash": "T1",
    "deepseek-flash-2": "T1",
    "deepseek-v4-flash-ascend": "T2",
    "deepseek-v4-flash-ascend1": "T2",
    "qwen3.8-flash-next-2": "T3",
    "qwen3.8-flash-next": "T3",
    "glm-5.3-flash": "T4",
    "qwen3.8-reasoner": "T5",
    "smart/default": "ref",
    "qwen3.8-chat": "ref",
}

MODELS = list(TIER.keys())
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
JSON_INSTR = '只输出 JSON，不要任何其他文字、不要markdown代码块：{"ok":true}'
BASIC_MSG = "只回复两个字：正常"


def call(model, messages, _key=None, **kw):
    """返回 dict: status, body(json或文本), elapsed_ms, err"""
    payload = {"model": model, "messages": messages}
    payload.update(kw)
    t0 = time.perf_counter()
    rec = {"model_sent": model, "payload_keys": sorted(payload.keys())}
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.post(
                f"{BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {_key or KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status"] = r.status_code
        raw = r.text
        rec["raw_len"] = len(raw)
        rec["raw_head"] = raw[:1200]
        try:
            rec["body"] = r.json()
        except Exception:
            rec["body"] = None
            rec["body_parse_error"] = True
        return rec
    except Exception as e:
        rec["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        rec["status"] = None
        rec["err"] = f"{type(e).__name__}: {e}"
        return rec


def extract(rec):
    """从 call() 结果里抽取给故障转移用的事实"""
    out = {
        "status": rec.get("status"),
        "elapsed_ms": rec.get("elapsed_ms"),
        "err": rec.get("err"),
        "finish_reason": None,
        "model_field": None,
        "content": None,
        "content_len": None,
        "content_empty": None,
        "reasoning_content_len": None,
        "usage": None,
        "raw_head": rec.get("raw_head"),
    }
    b = rec.get("body")
    if isinstance(b, dict):
        out["model_field"] = b.get("model")
        out["usage"] = b.get("usage")
        ch = b.get("choices")
        if isinstance(ch, list) and ch:
            c0 = ch[0] if isinstance(ch[0], dict) else {}
            out["finish_reason"] = c0.get("finish_reason")
            msg = c0.get("message") or {}
            if isinstance(msg, dict):
                c = msg.get("content")
                out["content"] = c
                out["content_len"] = len(c) if isinstance(c, str) else None
                out["content_empty"] = (not c) if isinstance(c, str) else None
                rc = msg.get("reasoning_content")
                out["reasoning_content_len"] = len(rc) if isinstance(rc, str) else None
        else:
            # 网关错误体
            e = b.get("error")
            if isinstance(e, dict):
                out["error_type"] = e.get("type")
                out["error_code"] = e.get("code")
                out["error_message"] = e.get("message")
            elif isinstance(b.get("detail"), (str, list)):
                out["error_type"] = "detail"
                out["error_message"] = str(b.get("detail"))[:400]
    return out


def main():
    results = {"base": BASE, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "models": {}}

    def save():
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    # 0. 连通性
    try:
        with httpx.Client(timeout=TIMEOUT) as c:
            r = c.get(f"{BASE}/models", headers={"Authorization": f"Bearer {KEY}"})
        results["models_endpoint"] = {"status": r.status_code, "body": r.json()}
        print(f"[env] GET /models -> {r.status_code}")
    except Exception as e:
        results["models_endpoint"] = {"err": f"{type(e).__name__}: {e}"}
        print(f"[env] GET /models FAILED: {e}")

    # 1. 错误形态（先测，独立于模型循环）
    errs = {}
    errs["nonexistent_model"] = call(
        "does-not-exist-xyz", [{"role": "user", "content": "hi"}], max_tokens=10
    )
    errs["empty_model"] = call("", [{"role": "user", "content": "hi"}], max_tokens=10)
    errs["bad_key"] = call(
        "deepseek-flash", [{"role": "user", "content": "hi"}], max_tokens=10,
        _key="sk-invalid-key-for-probe",
    )
    results["error_shapes"] = errs
    save()
    for k, v in errs.items():
        if v:
            print(f"[err-shape] {k}: HTTP={v.get('status')} raw={str(v.get('raw_head'))[:200]}")

    # 2. 逐模型
    for m in MODELS:
        entry = {"tier": TIER[m]}
        print(f"\n=== {m} ({TIER[m]}) ===")

        # 2.1 基础 chat x2
        basics = []
        for i in range(2):
            rec = call(m, [{"role": "user", "content": BASIC_MSG}], max_tokens=20)
            e = extract(rec)
            basics.append(e)
            print(f"  basic#{i+1}: HTTP={e['status']} {e['elapsed_ms']}ms "
                  f"content={e['content']!r} model_field={e['model_field']} usage={e['usage']}")
        entry["basic"] = basics

        # 2.2 JSON 遵从
        rec = call(m, [{"role": "user", "content": JSON_INSTR}], max_tokens=200,
                   temperature=0.0)
        e = extract(rec)
        parsed = None
        if e["content"]:
            try:
                parsed = json.loads(e["content"].strip())
            except Exception:
                try:
                    s = e["content"].strip()
                    if s.startswith("```"):
                        s = s.strip("`")
                        s = s.split("\n", 1)[-1] if "\n" in s else s
                    parsed = json.loads(s)
                except Exception:
                    parsed = None
        e["json_parsed"] = parsed
        e["json_ok"] = bool(isinstance(parsed, dict) and parsed.get("ok") is True)
        entry["json_mode"] = e
        print(f"  json: HTTP={e['status']} {e['elapsed_ms']}ms content={e['content']!r} parsed={parsed}")

        # 2.3 长输入
        rec = call(m, [{"role": "user", "content": LONG_INPUT}], max_tokens=200)
        e = extract(rec)
        e["prompt_chars"] = len(LONG_INPUT)
        entry["long_input"] = e
        print(f"  long({len(LONG_INPUT)}字): HTTP={e['status']} {e['elapsed_ms']}ms "
              f"finish={e['finish_reason']} clen={e['content_len']} content={(e['content'] or '')[:60]!r}")

        # 2.4 参数兼容: temperature + max_tokens
        rec = call(m, [{"role": "user", "content": "用一句话说明什么是递归。"}],
                   temperature=0.85, max_tokens=500)
        e = extract(rec)
        entry["params_temp"] = e
        print(f"  temp0.85/max500: HTTP={e['status']} {e['elapsed_ms']}ms clen={e['content_len']}")

        # 2.5 response_format json_object
        rec = call(m, [{"role": "user", "content": JSON_INSTR}], max_tokens=200,
                   response_format={"type": "json_object"})
        e = extract(rec)
        p2 = None
        if e["content"]:
            try:
                p2 = json.loads(e["content"].strip())
            except Exception:
                p2 = None
        e["json_parsed"] = p2
        e["supported"] = bool(e["status"] == 200 and p2 is not None)
        entry["response_format"] = e
        print(f"  response_format: HTTP={e['status']} {e['elapsed_ms']}ms "
              f"parsed={p2} err={e.get('error_message')}")

        results["models"][m] = entry
        save()
        sys.stdout.flush()

    # 3. 参考模型 smart/reasoning（不在 MODELS 里，单独补）
    for m in ["smart/reasoning"]:
        print(f"\n=== {m} (ref) ===")
        entry = {"tier": "ref"}
        basics = []
        for i in range(2):
            rec = call(m, [{"role": "user", "content": BASIC_MSG}], max_tokens=20)
            basics.append(extract(rec))
        entry["basic"] = basics
        results["models"][m] = entry
        print(f"  basic: {[(b['status'], b['content']) for b in basics]}")
        save()

    results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save()
    print(f"\n[done] -> {OUT}")


if __name__ == "__main__":
    main()
