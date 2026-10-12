#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 实弹驱动：用 json.dumps 构造合法 stdin，按机器人的方式子进程调用模型脚本。

用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_live_driver.py

说明：model_primary.py / model_fallback.py 不调用 load_dotenv()，靠父进程环境继承；
      机器人进程由 reverse_bot.py 的 load_dotenv() 注入。这里为复现同等条件先 load_dotenv()。
      密钥只进子进程 env，不打印。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    print("[env] 已 load_dotenv(.env)（值不打印）")
except Exception as e:  # pragma: no cover
    print(f"[env] load_dotenv 失败: {e}")

# ---------- 故障现场还原：10:25-10:26 真实群聊窗口（gid=284517232） ----------
CONTEXT = "\n".join([
    "[UC]: 软的翻出去",
    "[橘又青]: 我发现我关注的有点儿少了，有没有人能推荐点",
    "[UC]: 硬的翻回来",
    "[ㅤ]: 摇一摇开屏真是本世纪最阴间的发明，打个卡差点喜提三万，这波是广告商给你强制理财了嗝",
    "[多喝热水]: 后台可以关允许摇晃感应",
    "[橘又青]: 能不能变成贴吧鱼",
    "[爱莉不知道哦]: 怎么又一个哈耶克啊",
])
PERSONA = "你是群聊机器人，请用简短、口语化的中文回复。"
TARGET = {"nickname": "UC", "uid": "1305489180", "content": "今日深圳天气"}
AVOID = [
    "（尾巴一甩）摇一摇广告这玩意儿是真缺德，打个卡都能给你摇出三万块贷款，本鱼建议直接后台把传感器权限喂鱼算了",
    "摇一摇广告就是当代赛博碰瓷，手指一动三万块差点进账，这波是美团给你发的赛博彩礼吧",
]


def run(script: str, payload: dict, title: str) -> dict:
    stdin_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    print("=" * 70)
    print(title)
    print(f"脚本   : {script}")
    print(f"stdin  : {json.dumps(payload, ensure_ascii=False)}")
    proc = subprocess.run(
        [sys.executable, script],
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=os.environ.copy(),
    )
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    err = proc.stderr.decode("utf-8", errors="replace").strip()
    print(f"rc     : {proc.returncode}")
    print(f"stdout : {out}")
    print(f"stderr : {err}")
    try:
        reply = json.loads(out).get("reply", "")
    except Exception:
        reply = "<非 JSON 输出>"
    print(f"回复   : {reply if reply else '（空）'}")
    print()
    return {"title": title, "script": script, "stdin": payload,
            "stdout": out, "stderr": err, "rc": proc.returncode, "reply": reply}


results = []
results.append(run("scripts/model_primary.py", {
    "context": CONTEXT, "persona": PERSONA, "max_tokens": 80, "target": TARGET,
}, "用例1  有 target（复现故障现场）/ 主脚本 model_primary.py"))

results.append(run("scripts/model_primary.py", {
    "context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
}, "用例2  无 target（基线，应与旧版同构）/ 主脚本 model_primary.py"))

results.append(run("scripts/model_fallback.py", {
    "context": CONTEXT, "persona": PERSONA, "max_tokens": 80, "target": TARGET,
}, "用例3  有 target / 备脚本 model_fallback.py"))

results.append(run("scripts/model_fallback.py", {
    "context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
}, "用例4  无 target（基线）/ 备脚本 model_fallback.py"))

results.append(run("scripts/model_primary.py", {
    "context": CONTEXT, "persona": PERSONA, "max_tokens": 80,
    "target": TARGET, "avoid_recent": AVOID,
}, "用例5  有 target + avoid_recent（防复读注入）/ 主脚本"))

dump = os.path.join(ROOT, "qq_native_re", "findings", "reply_fix", "w1_live_results.json")
with open(dump, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"[落盘] {dump}")

# 文字判定
print("=" * 70)
print("自动判定：")
for r in results:
    rep = r["reply"] or ""
    hit_weather = ("天气" in rep) or ("深圳" in rep) or ("多云" in rep) or ("雨" in rep) or ("伞" in rep)
    hit_yao = "摇一摇" in rep
    print(f"  - {r['title'][:6]} rc={r['rc']} 空回复={not bool(rep)} "
          f"提到天气/深圳={hit_weather} 继续摇一摇={hit_yao} | {rep[:60]}")
