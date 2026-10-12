# -*- coding: utf-8 -*-
"""
模型池加固演练（scripts/test_pool_harden.py）—— W1b 新增。

W1 的 scripts/test_llm_pool.py（四场景 A/B/C/D）**保持原样不动**，本文件是
加固补丁的专项演练，四件事：

  H1 小 max_tokens 降档 —— 首档=不存在的模型，第二档只留 qwen3.8-flash-next-2，
     调用时传 max_tokens=16 → 必须仍拿到**非空**回复（证明下限生效，思考型
     模型不再被误判为空而冷却）。附带反证：绕过池、直连网关发 max_tokens=16
     看原始响应（应为 content 空 / finish_reason=length）。
  H2 请求侧错误不冻结 —— temperature="hot" 与 messages=[] 两种必触发
     litellm.BadRequestError 的请求 → 必须抛 LLMPoolRequestError（不是普通
     耗尽），且 cooldown_snapshot() 为空（整池没被冻）。
  H3 401 立即止损 —— 进程内把 api_key 覆盖为坏 key（**绝不改 .env**）
     → 必须抛 LLMPoolAuthError、冷却为空、且审计里只有 1 次尝试（不逐档重试）。
  H4 线上报文捕获（离线本地桩，不消耗网关额度）—— 用本地 HTTP 桩接住请求，
     证明 (a) max_tokens 下限真的落在 wire 上、(b) qwen3.8-flash-next 确实
     不带 response_format 而其它模型照常带。

运行：
  cd E:\\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\\venv\\Scripts\\python" scripts\\test_pool_harden.py
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import httpx  # noqa: E402

import llm_pool  # noqa: E402

# 当前定稿的模型优先级链（业务有意变更时必须同步更新此常量；
# H6 用它把“预期链”与实际配置比对，防止误改档位）
# 基准来源说明：H6 原以“W1 备份模块的硬编码 _FALLBACK_CONFIG”为基准，属一次性
# 兼容性检查；T5 档恢复为 [qwen3.8-reasoner, qwen-reasoner] 后该基准过期，会误报
# FAIL。现改为与下面这份显式声明比对，使 H6 成为“防止误改优先级链”的活回归测试。
EXPECTED_TIERS = [
    ["deepseek-flash", "deepseek-flash-2"],
    ["deepseek-v4-flash-ascend", "deepseek-v4-flash-ascend1"],
    ["qwen3.8-flash-next-2", "qwen3.8-flash-next"],
    ["glm-5.3-flash"],
    ["qwen3.8-reasoner", "qwen-reasoner"],
]

OK = "[PASS]"
NG = "[FAIL]"

_failures: list[str] = []


def _check(cond: bool, what: str) -> bool:
    print(f"  {OK if cond else NG} {what}")
    if not cond:
        _failures.append(what)
    return bool(cond)


def _mask(text: str) -> str:
    """打印前脱敏：报告/输出里 key 一律 sk-personal-****。"""
    out = str(text or "")
    for secret in (
        llm_pool.load_config().get("api_key") or "",
        llm_pool.os.environ.get("LLM_POOL_API_KEY", ""),
    ):
        if secret and len(secret) > 8:
            out = out.replace(secret, "sk-personal-****")
    return out


def _redact_body(body: str) -> str:
    """把 404 body 里的上游 key 内部名字打码（报告留证据但不泄露人名）。"""
    import re as _re

    body = _mask(body)
    return _re.sub(r'"key"\s*:\s*"[^"]*"', '"key":"<redacted>"', body)


@contextlib.contextmanager
def _temp_config(overrides: dict):
    """把 llm_pool 的配置切到临时文件（落在已确认可写的 findings 目录），退出还原。"""
    real_path = llm_pool.CONFIG_PATH
    base = json.loads(real_path.read_text(encoding="utf-8"))
    base.update(overrides)
    tmp = (
        llm_pool.PROJECT_ROOT
        / "qq_native_re"
        / "findings"
        / "model_pool"
        / "_drill_harden_model_pool.json"
    )
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
        with contextlib.suppress(OSError):
            tmp.write_text("", encoding="utf-8")


def _audit_snapshot() -> tuple:
    path = llm_pool.audit_log_path()
    if path is None or not path.exists():
        return (path, 0)
    return (path, path.stat().st_size)


def _audit_since(snap: tuple, label: str | None = None) -> list:
    path, offset = snap
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(offset)
        tail = fh.read()
    out = []
    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if label is None or rec.get("label") == label:
            out.append(rec)
    return out


# ---------------------------------------------------------------------------
# H1 小 max_tokens 降档
# ---------------------------------------------------------------------------


def drill_h1() -> None:
    print("\n=== H1 小 max_tokens 降档（思考型模型不再被判空） ===")
    model = "qwen3.8-flash-next-2"
    cfg = llm_pool.load_config()
    print(f"  配置下限: min_max_tokens={cfg['min_max_tokens']} "
          f"model_overrides={cfg['model_overrides']}")
    print(f"  {model} 在调用方传 16 时实际发送的 max_tokens: "
          f"{llm_pool.effective_max_tokens(model, 16)}")

    # 反证：绕过池直连网关，用 max_tokens=16 看原始响应（证明"给小了真会空"）
    raw_url = cfg["base_url"].rstrip("/") + "/chat/completions"
    raw_headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    raw_payload = {
        "model": model,
        "messages": [{"role": "user", "content": "只回复两个字：正常"}],
        "max_tokens": 16,
    }
    try:
        with httpx.Client(trust_env=False, timeout=30) as client:
            raw = client.post(raw_url, json=raw_payload, headers=raw_headers)
        raw_data = raw.json()
        raw_choice = (raw_data.get("choices") or [{}])[0]
        raw_content = (raw_choice.get("message") or {}).get("content")
        raw_finish = raw_choice.get("finish_reason")
        print(f"  [反证] 直连网关 max_tokens=16 -> HTTP {raw.status_code} "
              f"content={raw_content!r} finish_reason={raw_finish}")
        _check(
            not str(raw_content or "").strip(),
            "反证成立：直连网关 max_tokens=16 时 content 为空（这就是会被误判为故障的场景）",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  [反证] 直连网关失败（不影响主判定）: {type(exc).__name__}: {exc}")

    # 主判定：池调用，调用方传 16，首档是不存在的模型
    snap = _audit_snapshot()
    with _temp_config(
        {
            "tiers": [["no-such-model-xyz"], [model]],
            "attempt_timeout_seconds": 20,
            "total_budget_seconds": 40,
        }
    ):
        llm_pool.clear_cooldowns()
        print(f"  临时档位: [['no-such-model-xyz'], ['{model}']]，调用 max_tokens=16")
        t0 = time.time()
        text, used = llm_pool.chat_sync(
            [{"role": "user", "content": "只回复两个字：正常"}],
            label="drill-H1",
            max_tokens=16,
        )
        secs = time.time() - t0
        print(f"  实际服务模型: {used}")
        print(f"  回复内容: {text!r}  (耗时 {secs:.2f}s)")
        _check(used == model, f"降档后落在第二档 {model}: {used}")
        _check(bool(text.strip()), f"max_tokens=16 调用仍拿到非空回复: {text!r}")
        cool = llm_pool.cooldown_snapshot()
        print(f"  冷却快照: {cool}")
        _check(
            "no-such-model-xyz" in cool,
            "不存在的首档仍被正确长冷却（404 分流没坏）",
        )
        _check(model not in cool, f"{model} 没有被误冷却")

    recs = _audit_since(snap, label="drill-H1")
    print(f"  本次审计记录 {len(recs)} 条:")
    for rec in recs:
        print(f"    model={rec.get('model')} ok={rec.get('ok')} "
              f"err={_redact_body(str(rec.get('err')))}")
    errs = [str(r.get("err") or "") for r in recs if not r.get("ok")]
    if errs:
        longest = max(errs, key=len)
        print(f"  最长 err 字段: {len(longest)} 字符")
        _check(len(longest) <= 400, f"审计 err 已瘦身（{len(longest)} ≤ 400 字符）")
        _check(
            '"key"' not in longest and "attempts" not in longest
            and "can only access models" not in longest,
            "审计 err 里没有 404 body 的上游 key/内部结构",
        )
        _check(
            any(e.startswith("HTTP 404/") for e in errs),
            "404 的 err 格式为 `HTTP 404/<error.type>: <message>`",
        )
    llm_pool.clear_cooldowns()


# ---------------------------------------------------------------------------
# H2 请求侧错误不冻结
# ---------------------------------------------------------------------------


# 网关瞬时抖动的判据关键词：**只用于决定"要不要重试一次"**，
# 绝不参与断言条件的放松（断言集合首次/重试完全相同）。
_H2_TRANSIENT_MARKERS = ("503", "no_keys_available")


def _h2_rec_status(rec: dict) -> int:
    """从审计 err 的 `HTTP <status>/<type>: ...` 里取状态码；取不到返回 0。"""
    import re as _re

    match = _re.match(r"\s*HTTP\s+(\d{3})/", str(rec.get("err") or ""))
    return int(match.group(1)) if match else 0


def _h2_attempt(messages: list, label: str, kwargs: dict) -> dict:
    """跑一次 H2 请求并原样记录观测值（不做断言、不写 _failures）。

    每次都以清空冷却表开局，保证首次与重试的比较基准一致。
    """
    llm_pool.clear_cooldowns()
    snap = _audit_snapshot()
    t0 = time.time()
    raised = None
    try:
        llm_pool.chat_sync(messages, label=label, **kwargs)
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    return {
        "raised": raised,
        "cool": llm_pool.cooldown_snapshot(),
        "recs": _audit_since(snap, label=label),
        "secs": time.time() - t0,
    }


def _h2_dump(res: dict, when: str) -> None:
    raised = res["raised"]
    print(f"     [{when}] 异常类型: {type(raised).__name__ if raised else None}")
    print(f"     [{when}] 异常消息: {_mask(str(raised)) if raised else None}")
    print(f"     [{when}] 冷却快照: {res['cool']}")
    print(f"     [{when}] 审计记录数(尝试次数): {len(res['recs'])}  "
          f"耗时 {res['secs']:.2f}s")


def _h2_expectations(name: str, res: dict) -> dict:
    """H2 的既有断言逐条求值，返回 {键: (是否通过, 断言文案)}。

    本函数只求值不落盘，供"重试前先试算"使用；落盘统一走 _h2_emit，
    因此断言集合与改动前逐条一致（没有删减、没有放宽）。
    """
    raised = res["raised"]
    recs = res["recs"]
    detail = {
        "raised": (raised is not None, f"{name}: 抛出了异常"),
        "request_error": (
            isinstance(raised, llm_pool.LLMPoolRequestError),
            f"{name}: 异常是 LLMPoolRequestError（请求侧问题，非普通耗尽）",
        ),
        "not_bare_exhausted": (
            not type(raised) is llm_pool.LLMPoolExhausted,  # noqa: E721
            f"{name}: 不是裸的 LLMPoolExhausted（确实是新异常类）",
        ),
        "exhausted_subclass": (
            isinstance(raised, llm_pool.LLMPoolExhausted),
            f"{name}: 新异常是 LLMPoolExhausted 的子类（下游 except 不漏捕获）",
        ),
        "no_cooldown": (
            res["cool"] == {},
            f"{name}: 冷却快照为空（整池没被一次坏请求冻结）",
        ),
        "tried_all": (
            len(recs) == len(llm_pool.available_models()),
            f"{name}: 每个模型都试了一次且都没冷却（{len(recs)} 条审计）",
        ),
    }
    if raised is not None:
        detail["msg_is_request"] = (
            "请求本身有问题" in str(raised),
            f"{name}: 异常消息明确说明是请求问题不是模型故障",
        )
    return detail


def _h2_emit(detail: dict) -> bool:
    """落盘断言结果（通过/失败都进 _failures 统计）。"""
    for cond, what in detail.values():
        _check(cond, what)
    return all(cond for cond, _ in detail.values())


def _h2_invariant(name: str, res: dict, when: str) -> bool:
    """核心不变量（本次修复**新增的保护**，不是放宽）：

    请求侧错误绝不应触发 hard cooldown。短冷却（transient 60s）是设计内的，
    长冷却（model_missing 600s）落在本次请求上就是真缺陷 —— 首次与重试
    都独立断言，避免"抖动重试"把真实缺陷一起掩盖掉。
    """
    cfg = llm_pool.load_config()
    short = float(cfg["cooldown_seconds"])
    hard = float(cfg["hard_cooldown_seconds"])
    long_cooled = {m: rem for m, rem in res["cool"].items() if rem > short}
    if long_cooled:
        print(f"     长冷却违规明细（{when}）: {long_cooled}")
    return _check(
        not long_cooled,
        f"{name}: [{when}] 核心不变量：没有任何模型被长冷却"
        f"（>{short:.0f}s，即 hard_cooldown={hard:.0f}s）",
    )


def _h2_blame_gateway_flake(res: dict) -> tuple:
    """判定"不符合预期"能否归因于网关瞬时抖动（二选一判据，命中则重试一次）。

    判据一：异常文本或本次审计的 err 里出现 503 / no_keys_available；
    判据二：冷却快照里的模型集合，恰好等于本次审计中返回 5xx 的那些模型。
    """
    blob = " ".join(
        [str(res["raised"] or "")] + [str(r.get("err") or "") for r in res["recs"]]
    ).lower()
    hits = [m for m in _H2_TRANSIENT_MARKERS if m in blob]
    if hits:
        return True, f"判据一命中：异常/审计文本出现 {hits}"
    five_xx = {r.get("model") for r in res["recs"] if _h2_rec_status(r) >= 500}
    cooled = set(res["cool"])
    if five_xx and cooled == five_xx:
        return True, (f"判据二命中：冷却集合 {sorted(cooled)} "
                      f"恰等于审计中返回 5xx 的模型集合")
    return False, (
        f"两条判据均不成立（文本无 {list(_H2_TRANSIENT_MARKERS)}；"
        f"冷却集合 {sorted(cooled)} != 5xx 集合 {sorted(five_xx)}）→ 按真实缺陷定责"
    )


def _h2_case(name: str, label: str, messages: list, **kwargs) -> None:
    print(f"  -- 子用例 {name}")
    res = _h2_attempt(messages, label, kwargs)
    _h2_dump(res, "首次")
    detail = _h2_expectations(name, res)

    if not all(cond for cond, _ in detail.values()):
        blame, why = _h2_blame_gateway_flake(res)
        print(f"     首次未完全符合预期；抖动归因判定: {blame} —— {why}")
        if blame:
            # 抖动不能掩盖真实缺陷：首次的长冷却不变量照样独立断言落盘
            _h2_invariant(name, res, "首次")
            print("     判定为网关瞬时抖动 → 重试一次"
                  "（断言集合与首次完全相同，不放宽任何条件）")
            retry = _h2_attempt(messages, label=label, kwargs=kwargs)
            _h2_dump(retry, "重试")
            passed = _h2_emit(_h2_expectations(name, retry))
            _h2_invariant(name, retry, "重试")
            if passed:
                print("     首次因网关瞬时抖动未过，重试通过")
            else:
                print("     重试仍不符合预期 → FAIL（不因加了重试而放松判定）")
            return
        # 不可归因于环境抖动 → 直接用首次结果走严格断言（保持 FAIL 定责）
        _h2_emit(detail)
        _h2_invariant(name, res, "首次")
        return

    _h2_emit(detail)
    _h2_invariant(name, res, "首次")


def drill_h2() -> None:
    print("\n=== H2 请求侧错误不冻结整池（400 litellm.BadRequestError） ===")
    print(f"  池内模型数: {len(llm_pool.available_models())}")
    _h2_case(
        "temperature='hot'（非法参数）",
        "drill-H2a",
        [{"role": "user", "content": "只回复两个字：正常"}],
        temperature="hot",
    )
    _h2_case("messages=[]（空消息）", "drill-H2b", [])
    llm_pool.clear_cooldowns()


# ---------------------------------------------------------------------------
# H3 401 立即止损
# ---------------------------------------------------------------------------


def drill_h3() -> None:
    print("\n=== H3 401 立即止损（进程内换坏 key，不改 .env） ===")
    cfg = llm_pool.load_config()
    bad_key = "sk-drill-badkey-h3"
    url = cfg["base_url"].rstrip("/") + "/chat/completions"

    # 先直连确认坏 key 的真实状态码（证据）
    try:
        with httpx.Client(trust_env=False, timeout=30) as client:
            raw = client.post(
                url,
                json={
                    "model": "deepseek-flash",
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 16,
                },
                headers={
                    "Authorization": f"Bearer {bad_key}",
                    "Content-Type": "application/json",
                },
            )
        print(f"  [证据] 坏 key 直连网关 -> HTTP {raw.status_code}")
        print(f"         body: {_mask(raw.text)[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [证据] 坏 key 直连失败: {type(exc).__name__}: {exc}")

    original = llm_pool.os.environ.get("LLM_POOL_API_KEY")
    snap = _audit_snapshot()
    try:
        llm_pool.os.environ["LLM_POOL_API_KEY"] = bad_key
        llm_pool._CONFIG_CACHE = None
        llm_pool._CONFIG_MTIME = None
        print(f"  进程内 LLM_POOL_API_KEY 已覆盖为 {bad_key}"
              f"（.env 未改动，原值内存中保留 {len(original or '')} 字符）")
        print(f"  池内模型数（若逐档重试就会有这么多次尝试）: "
              f"{len(llm_pool.available_models())}")
        llm_pool.clear_cooldowns()
        t0 = time.time()
        raised = None
        try:
            llm_pool.chat_sync(
                [{"role": "user", "content": "只回复两个字：正常"}], label="drill-H3"
            )
        except BaseException as exc:  # noqa: BLE001
            raised = exc
        secs = time.time() - t0
        cool = llm_pool.cooldown_snapshot()
        recs = _audit_since(snap, label="drill-H3")
        print(f"  异常类型: {type(raised).__name__ if raised else None}")
        print(f"  异常消息: {_mask(str(raised)) if raised else None}")
        print(f"  冷却快照: {cool}")
        print(f"  审计尝试次数: {len(recs)}  耗时 {secs:.2f}s")
        for rec in recs:
            print(f"    model={rec.get('model')} ok={rec.get('ok')} "
                  f"err={_mask(str(rec.get('err')))}")

        _check(raised is not None, "抛出了异常")
        _check(
            isinstance(raised, llm_pool.LLMPoolAuthError),
            "异常是 LLMPoolAuthError（鉴权失败）",
        )
        _check(
            isinstance(raised, llm_pool.LLMPoolExhausted),
            "LLMPoolAuthError 是 LLMPoolExhausted 的子类（下游不漏捕获）",
        )
        _check(cool == {}, "冷却快照为空（鉴权失败不设任何冷却）")
        _check(len(recs) == 1, f"只尝试了 1 次、没有逐档重试（实际 {len(recs)} 次）")
        if raised is not None:
            msg = str(raised)
            _check("网关鉴权失败" in msg, "消息含「网关鉴权失败」")
            _check("LLM_POOL_API_KEY" in msg, "消息指向 .env 的 LLM_POOL_API_KEY")
    finally:
        if original is None:
            llm_pool.os.environ.pop("LLM_POOL_API_KEY", None)
        else:
            llm_pool.os.environ["LLM_POOL_API_KEY"] = original
        llm_pool._CONFIG_CACHE = None
        llm_pool._CONFIG_MTIME = None
        print("  已还原 LLM_POOL_API_KEY 并清空配置缓存")
    llm_pool.clear_cooldowns()


# ---------------------------------------------------------------------------
# H4 线上报文捕获（离线本地桩）
# ---------------------------------------------------------------------------

_CAPTURED: list = []


class _CaptureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            _CAPTURED.append(json.loads(raw))
        except ValueError:
            _CAPTURED.append({"_raw": raw[:200].decode("utf-8", "replace")})
        body = json.dumps(
            {
                "id": "capture",
                "model": "capture",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "index": 0,
                        "message": {"role": "assistant", "content": "捕获成功"},
                    }
                ],
                "usage": {"total_tokens": 3},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 静音
        return


def drill_h4() -> str:
    """返回状态串：done / skipped。"""
    print("\n=== H4 线上报文捕获（本地桩，证明下限与 response_format 开关落到 wire 上） ===")
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    except OSError as exc:
        print(f"  [跳过] 本机无法绑定回环端口: {type(exc).__name__}: {exc}")
        return "skipped"
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"  本地捕获桩: http://127.0.0.1:{port}/v1")
    base = f"http://127.0.0.1:{port}/v1"
    try:
        cases = [
            ("qwen3.8-flash-next（no_response_format 名单内）", "qwen3.8-flash-next", False),
            ("deepseek-flash（名单外，应照常带）", "deepseek-flash", True),
        ]
        for name, model, expect_rf in cases:
            _CAPTURED.clear()
            with _temp_config(
                {
                    "tiers": [[model]],
                    "base_url": base,
                    "api_key": "sk-drill-capture-local",
                    "total_budget_seconds": 15,
                    "attempt_timeout_seconds": 10,
                }
            ):
                llm_pool.clear_cooldowns()
                text, used = llm_pool.chat_sync(
                    [{"role": "user", "content": "只回复两个字：正常"}],
                    label="drill-H4",
                    max_tokens=16,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
            payload = _CAPTURED[0] if _CAPTURED else {}
            print(f"  -- {name}")
            print(f"     回复: {text!r} (model={used})")
            print(f"     捕获到的请求体: {json.dumps(payload, ensure_ascii=False)}")
            _check(used == model, f"{model}: 走的是捕获桩")
            _check(
                payload.get("max_tokens") == 512,
                f"{model}: 调用方传 16，wire 上 max_tokens={payload.get('max_tokens')}（下限 512 生效）",
            )
            if expect_rf:
                _check(
                    "response_format" in payload,
                    f"{model}: 照常带 response_format",
                )
            else:
                _check(
                    "response_format" not in payload,
                    f"{model}: 已按 no_response_format 剔除 response_format",
                )
            _check(
                payload.get("temperature") == 0.2,
                f"{model}: temperature 照常透传（{payload.get('temperature')}）",
            )
    finally:
        server.shutdown()
        server.server_close()
        llm_pool.clear_cooldowns()
    return "done"


# ---------------------------------------------------------------------------
# H5 400 两义分流自检（真实 body + PROBE 样本）
# ---------------------------------------------------------------------------

# 样本取自 PROBE.md §4.x（key 名已脱敏）
_PROBE_SAMPLES = [
    (
        "404 model_not_found（PROBE §4.1，含上游 key 名的长 body）",
        404,
        '{"error":{"message":"没有 Key 支持模型 does-not-exist-xyz","type":"model_not_found",'
        '"attempts":[{"key":"<上游key-1>","kind":"capability","code":403,'
        '"message":"key not allowed to access model. This key can only access models=[...]"}]}}',
        "model_missing",
    ),
    (
        "400 invalid_request_error + Invalid model name（PROBE §4.2，等价模型不存在）",
        400,
        '{"error":{"message":"/chat/completions: Invalid model name passed in model=. Call `/v1/models` '
        'to view available models for your key.","type":"invalid_request_error","param":null,"code":"400"}}',
        "model_missing",
    ),
    (
        "400 litellm.BadRequestError 参数非法（PROBE §4.3，请求侧问题）",
        400,
        '{"error":{"message":"litellm.BadRequestError: OpenAIException - Failed to deserialize the JSON '
        'body into the target type: invalid type: string \\"hot\\", expected f32 at line 1 column 106"}}',
        "request",
    ),
    (
        "400 litellm.BadRequestError messages 为空（PROBE §4.3，请求侧问题）",
        400,
        '{"error":{"message":"litellm.BadRequestError: OpenAIException - Validation: The \'messages\' '
        'field cannot be empty. At least one message is required."}}',
        "request",
    ),
    (
        "401 auth_error（PROBE §4.4，全局故障 → 立即止损）",
        401,
        '{"error":{"message":"invalid token","type":"auth_error"}}',
        "auth",
    ),
    (
        "503 no_keys_available（PROBE §4.6，可能是全局故障 → 只短冷却）",
        503,
        '{"error":{"message":"所有 Key 均不可用","type":"no_keys_available","attempts":[{"key":"<上游key-N>",'
        '"kind":"capability","code":403,"message":"key not allowed to access model. This key can only '
        'access models=[...]"}]}}',
        "transient",
    ),
    (
        "500 其它 5xx → 短冷却",
        500,
        '{"error":{"message":"internal error","type":"internal_server_error"}}',
        "transient",
    ),
    (
        "403 按模型的能力限制（不是全局鉴权故障，不能中止整池）",
        403,
        '{"error":{"message":"key not allowed to access model. This key can only access models=[\'a\']","type":"forbidden"}}',
        "model_missing",
    ),
    (
        "403 真鉴权语义（无权限）",
        403,
        '{"error":{"message":"invalid token","type":"auth_error"}}',
        "auth",
    ),
]

_ACTION_TEXT = {
    "auth": "立即中止整池（不冷却）→ LLMPoolAuthError",
    "request": "不冷却、换下一个；全中标 → LLMPoolRequestError",
    "model_missing": "长冷却 600s、换下一个",
    "transient": "短冷却 60s、换下一个",
}


def drill_h5() -> None:
    print("\n=== H5 错误分流表（真实 body 实测 + PROBE 样本逐条自检） ===")
    cfg = llm_pool.load_config()
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    print("  -- 真实网关实测 body")
    live_cases = [
        (
            'model=""（应为「模型不存在」语义）',
            {"model": "", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16},
            "model_missing",
        ),
        (
            'temperature="hot"（应为「请求侧问题」语义）',
            {
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
                "temperature": "hot",
            },
            "request",
        ),
        (
            "不存在的模型（应为「模型不存在」语义）",
            {
                "model": "no-such-model-xyz",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
            },
            "model_missing",
        ),
    ]
    for name, payload, expect in live_cases:
        try:
            with httpx.Client(trust_env=False, timeout=30) as client:
                resp = client.post(url, json=payload, headers=headers)
            etype, emsg = llm_pool._parse_error(resp.text)
            kind = llm_pool._classify_http_error(resp.status_code, etype, emsg, resp.text)
            print(f"     {name}")
            print(f"       HTTP {resp.status_code} | error.type={etype!r} | "
                  f"message={_redact_body(emsg)[:170]!r}")
            print(f"       body 长度 {len(resp.text)} 字符 -> 分流={kind}（{_ACTION_TEXT[kind]}）")
            _check(kind == expect, f"{name}: 分流为 {expect}（实际 {kind}）")
        except Exception as exc:  # noqa: BLE001
            _check(False, f"{name}: 实测失败 {type(exc).__name__}: {exc}")

    print("  -- PROBE.md 样本离线自检")
    for name, status, body, expect in _PROBE_SAMPLES:
        etype, emsg = llm_pool._parse_error(body)
        kind = llm_pool._classify_http_error(status, etype, emsg, body)
        slim = llm_pool._slim_err(status, etype, emsg, body)
        print(f"     {name}")
        print(f"       error.type={etype!r} -> 分流={kind}（{_ACTION_TEXT[kind]}）")
        print(f"       审计 err（{len(slim)} 字符）: {slim}")
        _check(kind == expect, f"{name}: 分流为 {expect}（实际 {kind}）")
        _check(
            len(slim) <= 400 and '"key"' not in slim,
            f"{name}: 审计 err 瘦身且不含上游 key",
        )


# ---------------------------------------------------------------------------
# H6 向后兼容自检（接口/审计向后兼容 + 优先级链与 EXPECTED_TIERS 比对）
# ---------------------------------------------------------------------------


def drill_h6(last_records: list) -> None:
    import importlib.util
    import inspect

    print("\n=== H6 向后兼容自检（对比 W1 备份 llm_pool.py） ===")
    backups = sorted(
        (llm_pool.PROJECT_ROOT / "qq_native_re" / "patches").glob(
            "model_migration_backup_*_harden/llm_pool.py"
        )
    )
    if not backups:
        _check(False, "找到 W1 备份 llm_pool.py 用于对比")
        return
    backup = backups[-1]
    print(f"  对比基准: {backup}")
    spec = importlib.util.spec_from_file_location("llm_pool_w1_backup", backup)
    old = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(old)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        _check(False, f"备份模块可导入（{type(exc).__name__}: {exc}）")
        return

    print("  -- 公开接口签名")
    # V1（findings/vision/V1_core.md）有意给 chat/chat_sync 追加了**末位带默认值**的
    # 关键字参数 vision=False（识图白名单，只改候选顺序）。这是像 EXPECTED_TIERS 一样的
    # 有意业务变更，故按下面规则豁免：把新增参数从新签名里剔除后，仍需与 W1 基准**逐字一致**
    # （不是放宽成"随便加参数"——参数名/顺序/默认值都必须逐字相同）。
    _V1_ADDED_PARAMS = {"chat": ["vision"], "chat_sync": ["vision"]}
    for name in ("chat", "chat_sync", "available_models", "load_config",
                 "cooldown_snapshot", "clear_cooldowns", "audit_log_path",
                 "get_active_model"):
        sig_old = str(inspect.signature(getattr(old, name)))
        sig_new_raw = str(inspect.signature(getattr(llm_pool, name)))
        added = _V1_ADDED_PARAMS.get(name) or []
        params = list(inspect.signature(getattr(llm_pool, name)).parameters.values())
        # 先校验"新增参数"确实是追加在末尾、且默认值就是约定的值
        ok_added = True
        for offset, pname in enumerate(added, start=1):
            idx = len(params) - len(added) + offset - 1
            if idx < 0:
                ok_added = False
                break
            param = params[idx]
            if param.name != pname or param.kind is not inspect.Parameter.KEYWORD_ONLY:
                ok_added = False
                break
            if param.default is not False:
                ok_added = False
                break
        # 从签名文本里**逐字**剔除新增参数（不能用 Signature.replace 重建：
        # 重建后注解变成未加引号的求值对象，str() 与备份的字符串注解写法不一致，
        # 会把"完全一致的签名"误判成不一致）
        sig_new = sig_new_raw
        if added:
            # 与 str(inspect.signature(...)) 的实际写法保持一致：
            # "vision: 'bool' = False) -> 'tuple[str, str]'"
            want_tail = "".join(f", {p}: 'bool' = False" for p in added)
            want_tail += ") -> 'tuple[str, str]'"
            if sig_new_raw.endswith(want_tail):
                sig_new = sig_new_raw[: -len(want_tail)] + ") -> 'tuple[str, str]'"
            else:
                ok_added = False  # 文本形态不符预期，拒绝"豁免"，避免变成橡皮章
        print(f"     {sig_new_raw}")
        if added:
            print(f"       （V1 有意追加 {added}：剔除后与 W1 基准逐字比对）")
            _check(ok_added, f"{name} 的 V1 新增参数 {added} 为末尾 keyword-only 且默认 False")
        # 注：参数名比对**不做脱敏**（key 只出现在函数体里，签名里没有敏感值）
        _check(
            sig_old == sig_new,
            f"{name} 签名与 W1 一致"
            + (f"（剔除有意新增的 {added} 后）" if added else ""),
        )

    print("  -- 返回结构 / 异常继承")
    _check(
        inspect.iscoroutinefunction(llm_pool.chat)
        and not inspect.iscoroutinefunction(llm_pool.chat_sync),
        "chat 是协程、chat_sync 是同步函数（调用约定不变）",
    )
    for exc_name in ("LLMPoolRequestError", "LLMPoolAuthError"):
        exc = getattr(llm_pool, exc_name)
        _check(issubclass(exc, llm_pool.LLMPoolExhausted),
               f"{exc_name} 是 LLMPoolExhausted 的子类")
        _check(not hasattr(old, exc_name), f"{exc_name} 是本次新增（W1 没有）")
    _check(
        not hasattr(old, "effective_max_tokens") and hasattr(llm_pool, "effective_max_tokens"),
        "effective_max_tokens 是本次新增的公开辅助函数",
    )

    print("  -- 审计字段")
    keys_seen: set = set()
    for rec in last_records:
        keys_seen |= set(rec.keys())
    required = {"ts", "label", "tier", "model", "ok", "latency_ms", "err", "tokens"}
    allowed = required | {"final"}
    print(f"     实际出现过的字段: {sorted(keys_seen)}")
    _check(required <= keys_seen, f"八个小字段齐全: {sorted(required)}")
    _check(keys_seen <= allowed, f"没有多出计划外字段: {sorted(keys_seen - allowed)}")

    print("  -- 配置兼容（既有键仍在、优先级链 == EXPECTED_TIERS）")
    cfg_new = llm_pool.load_config()
    # 展平 + 去重（保持出现顺序），与 llm_pool.available_models() 的语义一致
    expected_flat: list[str] = []
    for tier in EXPECTED_TIERS:
        for model in tier:
            if model not in expected_flat:
                expected_flat.append(model)
    flat_new = list(llm_pool.available_models())
    print(f"     预期链 EXPECTED_TIERS: {EXPECTED_TIERS}")
    print(f"     实际链 cfg['tiers']    : {[list(t) for t in cfg_new['tiers']]}")
    print(f"     预期展平（去重）: {expected_flat}")
    print(f"     实际展平（去重）: {flat_new}")
    _check(
        [list(t) for t in cfg_new["tiers"]] == [list(t) for t in EXPECTED_TIERS],
        "优先级链 tiers 与 EXPECTED_TIERS 逐档一致（含顺序与成员）",
    )
    _check(
        flat_new == expected_flat,
        f"available_models() 与 EXPECTED_TIERS 展平去重序列逐项一致（{len(flat_new)} 个）",
    )
    _check(
        len(set(flat_new)) == len(flat_new),
        f"available_models() 无重复模型（共 {len(flat_new)} 个）",
    )
    for key in ("attempt_timeout_seconds", "total_budget_seconds",
                "cooldown_seconds", "hard_cooldown_seconds", "max_tokens_default"):
        _check(key in cfg_new, f"既有配置键仍在: {key}")


def main() -> int:
    print("=" * 72)
    print("模型池加固演练 scripts/test_pool_harden.py（W1b）")
    print("=" * 72)
    print("环境: LLM_POOL_DISABLE =", repr(llm_pool.os.environ.get("LLM_POOL_DISABLE")))
    print("配置: config/model_pool.json ->", llm_pool.CONFIG_PATH)
    print("审计: 首选", llm_pool.LOG_PATH, "-> 实际", llm_pool.audit_log_path())
    cfg = llm_pool.load_config()
    print(f"加固配置: min_max_tokens={cfg['min_max_tokens']} | "
          f"model_overrides={cfg['model_overrides']} | "
          f"no_response_format={cfg['no_response_format']}")

    snap_all = _audit_snapshot()
    drill_h1()
    drill_h2()
    drill_h3()
    h4 = drill_h4()
    drill_h5()
    drill_h6(_audit_since(snap_all))

    print("\n" + "=" * 72)
    print(f"H4 报文捕获: {h4}")
    if _failures:
        print(f"结果: {len(_failures)} 项未通过")
        for item in _failures:
            print(f"  - {item}")
        print("=" * 72)
        return 1
    print("结果: 全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
