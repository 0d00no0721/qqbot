#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 边界探针：把真实群聊里的长 CQ 消息当作 target.content，看模型是否被带偏。

真实素材（取自 2026-10-12 NapCat 日志 群284517232）：
  - UC 10:25:42 发的是 [CQ:image,file=...,url=https://multimedia.nt.qq.com.cn/download?appid=1407&...]
  - EVEN 10:26:18 发的是 [CQ:json,data={"ver":"1.0.0.19","prompt":"[QQ小程序]民族主义：一剂永不退场的毒药",...}]

关注点：这些消息被 @bot 触发后，target.content 会是一大串 CQ 码。
若模型被带偏（去念叨 CQ 码/URL/JSON），说明需要在 decision_engine 侧对 target.content 做清洗。
用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_edge_probe.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception as e:
    print(f"[env] load_dotenv 失败: {e}")

CONTEXT = "\n".join([
    "[UC]: 软的翻出去",
    "[橘又青]: 我发现我关注的有点儿少了，有没有人能推荐点",
    "[ㅤ]: 摇一摇开屏真是本世纪最阴间的发明，打个卡差点喜提三万",
    "[多喝热水]: 后台可以关允许摇晃感应",
])
PERSONA = "你是群聊机器人，请用简短、口语化的中文回复。"

IMG_CQ = ("[CQ:image,file=97E9C211D79BFFA3654E12CE5EC21C99.png,sub_type=0,"
          "url=https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=EpABCg9wcml2YXRlXzEwMjRfMA)")
JSON_CQ = ('[CQ:json,data={"ver":"1.0.0.19","prompt":"[QQ小程序]民族主义：一剂永不退场的毒药",'
           '"config":{"type":"normal"},"app":"com.tencent.miniapp"}]')
REPLY_CQ = "[CQ:reply,id=10016][CQ:at,qq=10013] \n我不好截图，等下群又封了"
LONG_AVOID = "摇一摇广告就是当代赛博碰瓷，" + "手指一动三万块差点进账，" * 30 + "这波是美团给你发的赛博彩礼吧"


def run(title, payload, script="scripts/model_primary.py"):
    print("=" * 74)
    print(title)
    # 注意：这里 str() 只是给探针自己算长度用——content 可能是 int/dict/None。
    tgt = payload.get("target")
    tcontent = tgt.get("content") if isinstance(tgt, dict) else None
    print(f"target.content 类型 = {type(tcontent).__name__}  长度 = {len(str(tcontent)) if tcontent is not None else 'N/A'}")
    print(f"stdin 总长度 = {len(json.dumps(payload, ensure_ascii=False))}")
    p = subprocess.run([sys.executable, script],
                       input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       cwd=ROOT, env=os.environ.copy())
    out = p.stdout.decode("utf-8", errors="replace").strip()
    err = p.stderr.decode("utf-8", errors="replace").strip()
    try:
        reply = json.loads(out).get("reply", "")
    except Exception:
        reply = f"<非 JSON: {out[:120]}>"
    print(f"rc={p.returncode}  stderr={err}")
    print(f"回复: {reply}")
    # 污染判定：回复里出现 CQ/url/json/appid 等噪声特征
    noise = [k for k in ("CQ:", "http", "appid", "json", "png", "prompt", "url=", "1407") if k in reply]
    print(f"污染特征: {noise if noise else '无'}")
    # 定向/回落 判定（读子进程 stderr 的自我报告）
    routed = "是" if "定向回复=是" in err else ("否" if "定向回复=否" in err else "?")
    print(f"子进程自报走的路: {routed}")
    print()
    return {"title": title, "reply": reply, "noise": noise, "routed": routed,
            "stdin_len": len(json.dumps(payload, ensure_ascii=False))}


res = []
res.append(run("边界1  target.content = 图片 CQ 码（UC 10:25:42 真实形态）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": IMG_CQ}}))

res.append(run("边界2  target.content = QQ小程序 JSON 卡片（EVEN 10:26:18 真实形态）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "EVEN", "uid": "10001", "content": JSON_CQ}}))

res.append(run("边界3  target.content = reply+at 前缀（UC 10:25:00 真实形态）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": REPLY_CQ}}))

res.append(run("边界4  nickname 为空（reverse_bot 里 sender.nickname 可能缺失）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "", "uid": "10011", "content": "今日深圳天气"}}))

res.append(run("边界5  avoid_recent 注入超长回复（prompt 膨胀压力）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": "今日深圳天气"},
                "avoid_recent": [LONG_AVOID]}))

res.append(run("边界6  content 为非字符串（int）—— 子进程健壮性",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": 12345}}))

res.append(run("边界7  target 为非 dict（字符串）—— 子进程健壮性",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": "今日深圳天气"}))

res.append(run("边界8  content 为 null —— 应回落旧 prompt（定向回复=否）",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": None}}))

res.append(run("边界9  content 为 dict —— 子进程健壮性",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": {"a": 1}}}))

res.append(run("边界10  target 为非 dict（字符串）/ 备脚本 —— 与主脚本一致",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": "今日深圳天气"}, script="scripts/model_fallback.py"))

res.append(run("边界11  content 为 null / 备脚本 —— 应回落旧 prompt",
               {"context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
                "target": {"nickname": "UC", "uid": "10011", "content": None}},
               script="scripts/model_fallback.py"))

dump = os.path.join(ROOT, "qq_native_re", "findings", "reply_fix", "w1_edge_results.json")
with open(dump, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
print(f"[落盘] {dump}")
print()
print("污染汇总：")
for r in res:
    print(f"  {'⚠ 有噪声' if r['noise'] else '✓ 干净  '}  {r['title'][:40]} | {r['reply'][:70]}")
