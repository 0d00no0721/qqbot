#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 回归：按「机器人父进程」的方式跑两个既有测试套件。

用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_regression.py

要点：
  - 两个测试脚本用 subprocess 直跑（脚本内依赖 __file__，不能被 exec）；
  - 先 load_dotenv('.env')，让子进程继承模型池密钥——与 reverse_bot.py 的
    load_dotenv() 再 spawn 子进程的条件一致（这两个测试脚本自身不调 load_dotenv）；
  - 串行执行：共享同一个模型池，并行会互相触发冷却导致假失败。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    print("[env] 已 load_dotenv(.env)（值不打印）")
except Exception as e:
    print(f"[env] load_dotenv 失败: {e}")

SUITES = ["scripts/test_llm_pool.py", "scripts/test_pool_harden.py"]
env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
summary = []

for s in SUITES:
    print("=" * 78)
    print(f"### 运行 {s}")
    print("=" * 78)
    p = subprocess.run([sys.executable, s], cwd=ROOT, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = p.stdout.decode("utf-8", errors="replace")
    print(text)
    print(f"--- {s} 退出码 = {p.returncode} ---")
    print()
    summary.append((s, p.returncode, text))

print("=" * 78)
print("回归汇总：")
for s, rc, _ in summary:
    print(f"  {'PASS' if rc == 0 else 'FAIL'}  {s}  (exit={rc})")

out = os.path.join(ROOT, "qq_native_re", "findings", "reply_fix", "w1_regression_output.txt")
with open(out, "w", encoding="utf-8") as f:
    for s, rc, text in summary:
        f.write(f"===== {s} (exit={rc}) =====\n{text}\n")
print(f"[落盘] {out}")
sys.exit(0 if all(rc == 0 for _, rc, _ in summary) else 1)
