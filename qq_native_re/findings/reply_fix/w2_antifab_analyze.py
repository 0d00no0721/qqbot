# -*- coding: utf-8 -*-
"""W2 分析：用同一套正则，对「改动前(W1)」与「改动后(W2)」的回放输出算编造率。

· 改动前 = qq_native_re/findings/reply_fix/replay_results_w1.json（W1 那轮跑出来的、旧路 B prompt 的输出）
· 改动后 = qq_native_re/findings/reply_fix/replay_results.json（本轮 W2 跑出来的，含 old/new 两臂）
判定函数直接复用台架 _replay.judge_fabrication / judge_target（同一口径，不另写一份）。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from _replay import judge_fabrication, judge_target  # noqa: E402

# 补充口径：中文数字温度（如「三十来度」），阿拉伯数字正则抓不到
CN_NUM_TEMP = re.compile(r"[一二三四五六七八九十两]+(?:来|几|多)?\s*(?:度|℃|°C)")


def scan(path, tag):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for vname, arms in data.get("results", {}).items():
        for arm, blob in arms.items():
            if tag == "W1" and arm != "new":
                continue  # W1 存档只关心当时的新 prompt
            if tag == "W2" and arm != "new":
                continue
            for r in blob.get("runs", []):
                reply = r.get("reply") or ""
                fab, hits = judge_fabrication(reply)
                cn = CN_NUM_TEMP.findall(reply)
                rows.append({
                    "variant": vname, "run": r.get("run"), "reply": reply,
                    "target": judge_target(reply), "fab": fab,
                    "hits": [h["match"] for h in hits], "cn_num": cn,
                    "ok": r.get("ok", True),
                })
    return rows


def report(rows, title):
    fab = [r for r in rows if r["fab"]]
    cn = [r for r in rows if r["cn_num"] and not r["fab"]]
    tgt = [r for r in rows if r["target"]]
    print(f"\n===== {title} =====")
    print(f"  成功 {sum(1 for r in rows if r['ok'])}/{len(rows)} | "
          f"回应 target {len(tgt)}/{len(rows)} | "
          f"正则判编造 {len(fab)}/{len(rows)} | 中文数字温度(正则外) {len(cn)}")
    for r in rows:
        mark = "编造" if r["fab"] else ("中文数字" if r["cn_num"] else "无数字")
        print(f"   - [{r['variant']} #{r['run']}] target={r['target']} {mark} "
              f"{r['hits'] or r['cn_num'] or ''} :: {r['reply'][:80]}")
    return {"n": len(rows), "target": len(tgt), "fab": len(fab), "cn_num": len(cn)}


before = report(scan(os.path.join(HERE, "replay_results_w1.json"), "W1"),
                "改动前：W1 路 B prompt（存档 replay_results_w1.json）")
after = report(scan(os.path.join(HERE, "replay_results.json"), "W2"),
               "改动后：W2 路 B prompt（本轮 replay_results.json new 臂）")

print("\n===== W2 编造率结论 =====")
print(f"  W1（改前）: 编造 {before['fab']}/{before['n']}"
      f"（另有人类判定计入的中文数字温度 {before['cn_num']} 条，正则抓不到）")
print(f"  W2（改后）: 编造 {after['fab']}/{after['n']}，回应 target {after['target']}/{after['n']}")
