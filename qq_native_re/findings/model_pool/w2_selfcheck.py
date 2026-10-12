# -*- coding: utf-8 -*-
r"""W2 自检脚本（主聊天链路接线后）。

沙箱限制说明（两条，均与本次改动无关，实测于 2026-10-12）：
  1) 受限进程（pwsh / venv python）只能写 E:\QQbot\qq_native_re\**，
     写 logs\ / config\ / scripts\ 一律 PermissionError。而 scripts/decision_engine.py
     在模块级就会为 logs\bot.<date>.log 建 logging.FileHandler，
     导致直接 `import reverse_bot` 抛 PermissionError。
  2) asyncio.create_subprocess_exec 在本沙箱下必然 WinError 5（即使
     `python -c print(42)` 这种无关命令也失败）；普通 subprocess.Popen 正常。
     真实 bot 不受限（非沙箱进程启动），该调用方式与改动前完全一致。

因此本脚本做两处**仅限测试进程**的替换后再验证，其余全部是真实链路：
  - logging.FileHandler -> NullHandler（避开 logs\ 写权限）
  - asyncio.create_subprocess_exec -> subprocess.Popen 版 shim（避开 asyncio 管道限制）
被替换的两处都不是本次改动的内容（见 git diff）。

用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; .\venv\Scripts\python qq_native_re\findings\model_pool\w2_selfcheck.py
"""

import asyncio
import json
import logging
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
)
sys.path.insert(0, PROJECT_ROOT)

print("PROJECT_ROOT =", PROJECT_ROOT)


# --- 沙箱探针 1：asyncio 子进程 ---
async def _probe_asyncio_spawn() -> None:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print(42)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate(b"")
        print("[0] asyncio.create_subprocess_exec 探针 -> OK", out.strip())
    except Exception as exc:  # noqa: BLE001
        print(f"[0] asyncio.create_subprocess_exec 探针 -> 沙箱拒绝 "
              f"{type(exc).__name__}: {exc}")


# --- 沙箱探针 2：写 logs\ / config\ ---
def _probe_writes() -> None:
    for path in ("logs/_w2_probe.tmp", "config/_w2_probe.tmp",
                 "qq_native_re/findings/model_pool/_w2_probe.tmp"):
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x")
            os.remove(path)
            print(f"[0] 写探针 {path} -> OK")
        except OSError as exc:
            print(f"[0] 写探针 {path} -> 沙箱拒绝 {type(exc).__name__}: {exc}")


async def _amain() -> None:
    await _probe_asyncio_spawn()
    _probe_writes()

    # ===== asyncio 子进程 shim（Popen 版，仅测试进程内生效） =====
    captured: list[str] = []

    class _Proc:
        def __init__(self, popen: subprocess.Popen) -> None:
            self._p = popen
            self.returncode = None

        async def communicate(self, input: bytes = b""):
            captured.append(input.decode("utf-8", errors="replace"))
            out, err = await asyncio.to_thread(self._p.communicate, input)
            self.returncode = self._p.returncode
            return out, err

    async def _shim(*args, **kwargs):
        for key in ("stdin", "stdout", "stderr"):
            kwargs.pop(key, None)
        popen = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
        return _Proc(popen)

    asyncio.create_subprocess_exec = _shim  # type: ignore[assignment]

    # ===== 1) 入口自检 =====
    # 仅测试进程内：把写 logs\ 的文件日志处理器换成 NullHandler（沙箱无 logs\ 写权限）
    logging.FileHandler = lambda *a, **k: logging.NullHandler()  # noqa: E731
    import reverse_bot  # noqa: F401,E402

    print("[1] import reverse_bot: OK")

    from scripts.decision_engine import DecisionEngine  # noqa: E402

    print("[2] AVAILABLE_MODELS =", DecisionEngine.AVAILABLE_MODELS)
    cfg = DecisionEngine.get_active_model()
    print("[2] get_active_model =", {
        "model": cfg["model"],
        "api_base_url": cfg["api_base_url"],
        "api_key": "sk-personal-****" if cfg["api_key"] else "",
    })
    print("[2] set_active_model('deepseek-v4-pro') ->",
          DecisionEngine.set_active_model("deepseek-v4-pro"))
    print("[2] set_active_model('deepseek-flash') ->",
          DecisionEngine.set_active_model("deepseek-flash"))

    # ===== 3) 真实主链路：引擎 -> 子进程 -> 本地网关模型池 =====
    engine = DecisionEngine()
    gid = "w2_selfcheck_group"
    engine.add_message(gid, "u1", "小明", "今天天气真好啊")
    engine.add_message(gid, "u2", "小红", "是啊适合出去玩")

    primary = await engine._call_model_script(engine._primary_script, gid, 50)
    print("[3] _call_model_script(model_primary)  ->", repr(primary))

    fallback = await engine._call_model_script(engine._fallback_script, gid, 50)
    print("[3] _call_model_script(model_fallback) ->", repr(fallback))

    reply = await engine._generate_reply_with_failover(gid)
    print("[3] _generate_reply_with_failover ->", repr(reply))

    # 3b) 主脚本路径失效 -> 验证「主失败降级到备用」这层熔断仍然有效
    real_primary = engine._primary_script
    engine._primary_script = real_primary + ".missing"
    try:
        reply2 = await engine._generate_reply_with_failover(gid)
    finally:
        engine._primary_script = real_primary
    print("[3b] 主脚本失效时 _generate_reply_with_failover ->", repr(reply2))

    print("[3] 三次调用全部拿到非空回复:",
          bool(primary and fallback and reply and reply2))

    # ===== 4) 下发给子进程的 stdin JSON 原文（key 脱敏） =====
    for raw in captured[:4]:
        data = json.loads(raw)
        if data.get("api_key"):
            data["api_key"] = "sk-personal-****"
        print("[4] stdin JSON:", json.dumps(
            {k: (v[:40] + "...") if k in ("context", "persona") and len(v) > 40 else v
             for k, v in data.items()}, ensure_ascii=False))
        break


asyncio.run(_amain())
print("SELFCHECK DONE")
