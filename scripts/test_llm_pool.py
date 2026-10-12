# -*- coding: utf-8 -*-
"""
中央模型池演练（scripts/test_llm_pool.py）

四个场景：
  A 正常调用        —— 拿真实回复，打印实际服务模型
  B 强制降档        —— 进程内把首档换成不存在的模型，验证仍能拿到回复且落在 T2+
  C 全挂            —— 所有档都不存在，验证抛 LLMPoolExhausted 且带失败明细
  D 审计            —— 打印 logs/llm_pool.jsonl 最后 5 行

注意：B/C 通过 monkeypatch llm_pool.CONFIG_PATH 指向临时配置文件实现，
**绝不修改 config/model_pool.json 本身**。

运行：
  cd E:\\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\\venv\\Scripts\\python" scripts\\test_llm_pool.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 允许从项目根或 scripts/ 目录直接运行
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import llm_pool  # noqa: E402

OK = "[PASS]"
NG = "[FAIL]"

_failures: list[str] = []


def _check(cond: bool, what: str) -> None:
    print(f"  {OK if cond else NG} {what}")
    if not cond:
        _failures.append(what)


def _with_temp_config(overrides: dict):
    """上下文管理器：把 llm_pool 的配置切到临时文件，退出时还原。

    临时文件直接落在已确认可写的 qq_native_re/findings/model_pool/ 下
    （不写系统 temp 目录、不写 logs/、也不新建子目录——本机沙箱对新建
    目录不授予写入权）。
    """
    import contextlib

    @contextlib.contextmanager
    def _cm():
        real_path = llm_pool.CONFIG_PATH
        base = json.loads(real_path.read_text(encoding="utf-8"))
        base.update(overrides)
        tmp = llm_pool.PROJECT_ROOT / "qq_native_re" / "findings" / "model_pool" / "_drill_model_pool.json"
        tmp.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        llm_pool.CONFIG_PATH = tmp
        llm_pool._CONFIG_CACHE = None
        llm_pool._CONFIG_MTIME = None
        try:
            yield tmp
        finally:
            llm_pool.CONFIG_PATH = real_path
            llm_pool._CONFIG_CACHE = None
            llm_pool._CONFIG_MTIME = None
            try:
                tmp.write_text("", encoding="utf-8")
            except OSError:
                pass

    return _cm()


def scenario_a() -> None:
    print("\n=== A 正常调用 ===")
    cfg = llm_pool.load_config()
    print(f"  池内模型（优先级序）: {llm_pool.available_models()}")
    print(f"  base_url: {cfg['base_url']}")
    t0 = time.time()
    text, model = llm_pool.chat_sync(
        [{"role": "user", "content": "只回复两个字：正常"}], label="drill-A"
    )
    secs = time.time() - t0
    print(f"  实际服务模型: {model}")
    print(f"  回复内容: {text!r}  (耗时 {secs:.2f}s)")
    t1 = set(cfg["tiers"][0])
    _check(model in t1, f"实际服务模型落在 T1（{sorted(t1)}）: {model}")
    _check(bool(text.strip()), "拿到了非空回复")


def scenario_b() -> None:
    print("\n=== B 强制降档（首档指向不存在的模型） ===")
    cfg = llm_pool.load_config()
    t2plus = [m for tier in cfg["tiers"][1:] for m in tier]
    with _with_temp_config({"tiers": [["no-such-model-xyz"], *cfg["tiers"][1:]]}):
        llm_pool.clear_cooldowns()
        print(f"  临时首档: ['no-such-model-xyz']，其余档不变")
        print(f"  临时池内模型: {llm_pool.available_models()}")
        t0 = time.time()
        text, model = llm_pool.chat_sync(
            [{"role": "user", "content": "只回复两个字：正常"}], label="drill-B"
        )
        secs = time.time() - t0
        print(f"  实际服务模型: {model}")
        print(f"  回复内容: {text!r}  (耗时 {secs:.2f}s)")
        _check(model != "no-such-model-xyz", "没有落在那个不存在的模型上")
        _check(model in t2plus, f"实际服务模型落在 T2 及以后: {model}")
        _check(bool(text.strip()), "降档后依然拿到了非空回复")
        print(f"  冷却快照: {llm_pool.cooldown_snapshot()}")
    llm_pool.clear_cooldowns()


def scenario_c() -> None:
    print("\n=== C 全挂（所有档都不存在） ===")
    bogus = [["no-such-model-xyz"], ["no-such-model-abc"], ["no-such-model-123"]]
    with _with_temp_config({"tiers": bogus, "attempt_timeout_seconds": 8, "total_budget_seconds": 30}):
        llm_pool.clear_cooldowns()
        print(f"  临时档位: {bogus}")
        t0 = time.time()
        raised = None
        try:
            llm_pool.chat_sync(
                [{"role": "user", "content": "只回复两个字：正常"}], label="drill-C"
            )
        except llm_pool.LLMPoolExhausted as exc:
            raised = exc
        secs = time.time() - t0
        _check(raised is not None, "抛出了 LLMPoolExhausted")
        if raised is not None:
            msg = str(raised)
            print(f"  异常类型: LLMPoolExhausted")
            print(f"  异常消息: {msg}")
            _check(
                all(m[0] in msg for m in bogus),
                "消息里能看到每个模型的失败原因",
            )
            print(f"  冷却快照: {llm_pool.cooldown_snapshot()}")
        print(f"  耗时 {secs:.2f}s")
    llm_pool.clear_cooldowns()


def scenario_d() -> None:
    print("\n=== D 审计（llm_pool.jsonl 最后 5 行） ===")
    path = llm_pool.audit_log_path()
    print(f"  首选位置: {llm_pool.LOG_PATH}")
    print(f"  实际写入: {path}")
    if path is None:
        _check(False, "存在可写的审计日志位置")
        return
    if not path.exists():
        _check(False, "审计日志文件存在")
        return
    size = path.stat().st_size
    lines = path.read_text(encoding="utf-8").splitlines()
    print(f"  文件大小: {size} bytes, 总行数: {len(lines)}")
    for line in lines[-5:]:
        try:
            rec = json.loads(line)
            print(
                "   "
                f"{rec.get('ts')} | label={rec.get('label')} | tier={rec.get('tier')} | "
                f"model={rec.get('model')} | ok={rec.get('ok')} | {rec.get('latency_ms')}ms | "
                f"tokens={rec.get('tokens')} | err={rec.get('err')}"
            )
        except ValueError:
            print(f"   [无法解析] {line[:160]}")
    _check(len(lines) > 0, "审计日志里有记录")


def main() -> int:
    print("=" * 72)
    print("中央模型池演练 scripts/test_llm_pool.py")
    print("=" * 72)
    print("环境: LLM_POOL_DISABLE =", repr(llm_pool.os.environ.get("LLM_POOL_DISABLE")))
    print("配置: config/model_pool.json ->", llm_pool.CONFIG_PATH)
    print("审计: 首选", llm_pool.LOG_PATH, "-> 实际", llm_pool.audit_log_path())

    scenario_a()
    scenario_b()
    scenario_c()
    scenario_d()

    print("\n" + "=" * 72)
    if _failures:
        print(f"结果: {len(_failures)} 项未通过")
        for f in _failures:
            print(f"  - {f}")
        print("=" * 72)
        return 1
    print("结果: 全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
