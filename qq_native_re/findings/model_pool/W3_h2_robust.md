# W3 — H2 子用例网关抖动假红修复（scripts/test_pool_harden.py）

状态：**完成**（两次真跑全绿 + 4 场景故障注入证明仍能抓真缺陷）
时间：2026-10-12 08:41–08:52

## 0. 备份

- 目录：`E:\QQbot\qq_native_re\patches\model_migration_backup_20261012-084134_h2\`
- 文件：`test_pool_harden.py`（30659 字节）
- 改前 SHA256：`7D5D0BBFC3E96933E07F4D21910EC26BFED3121D8F3B4D791FBE7FC67E5DAB59`
- 改后 SHA256：`8ABAEA2703FEF4D09A8C1A182FA2B6E6FB5B13472EA3438A0D3FEEF0E9F03E60`（883 行，+148/−28）

## 1. 问题定性（沿用上位结论，未重新定性）

H2 期望「请求侧错误 → `LLMPoolRequestError` + `cooldown_snapshot() == {}`」。
真实网关偶发让**某一个**模型瞬时 503/no_keys_available，池按设计给该模型 60s 短冷却
（`llm_pool.py:966-968` transient 分流），于是整池耗尽成 `LLMPoolExhausted`
（`llm_pool.py:1031`）→ 假红。**池行为正确，测试假设过严。**

## 2. 改动（只动 H2：旧 242–284 行 → 新 244–404 行）

`git diff --no-index -U0` 全部 hunk 都落在旧 242→321 行区间（即 `_h2_case` 及其上方新增辅助函数），
H1/H3/H4/H5/H6 一行未动：

```
@@ -242,2 +242,18 @@   @@ -252,27 +268,48 @@   @@ -280 +317 @@   @@ -283,0 +321,83 @@
```

新增函数（均在 H2 区块内）：`_h2_rec_status`(247) `_h2_attempt`(255) `_h2_dump`(276)
`_h2_expectations`(285) `_h2_emit`(324) `_h2_invariant`(331) `_h2_blame_gateway_flake`(351)，
重写 `_h2_case`(374)。**7 条原断言逐条原样保留**（文案、条件均未改），只是拆成
"先求值（`_h2_expectations`）→ 符合则落盘（`_h2_emit`）"两步，以便重试前试算。

### 2.1 改动前（旧 `_h2_case`，节选）

```python
def _h2_case(name: str, label: str, messages: list, **kwargs) -> None:
    print(f"  -- 子用例 {name}")
    llm_pool.clear_cooldowns()
    snap = _audit_snapshot(); t0 = time.time(); raised = None
    try:
        llm_pool.chat_sync(messages, label=label, **kwargs)
    except BaseException as exc:
        raised = exc
    ...
    _check(cool == {}, f"{name}: 冷却快照为空（整池没被一次坏请求冻结）")
    _check(len(recs) == len(llm_pool.available_models()), ...)
    # ← 一次性判定：网关任一瞬间抖动即假红
```

### 2.2 改动后（新 `_h2_case`，全文）

```python
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
```

### 2.3 「重试仍不符预期则 FAIL」分支（证明没改成永远通过）

```python
            passed = _h2_emit(_h2_expectations(name, retry))   # 重试结果走**同一套**断言
            _h2_invariant(name, retry, "重试")
            if passed:
                print("     首次因网关瞬时抖动未过，重试通过")
            else:
                print("     重试仍不符合预期 → FAIL（不因加了重试而放松判定）")
```

`_h2_emit` 内部是 `for cond, what in detail.values(): _check(cond, what)` —— 与改动前
完全相同的 7 条 `_check`；重试不通过时这 7 条会照常进 `_failures`，脚本退出码为 1。

### 2.4 抖动归因判据（二选一，仅用于"要不要重试"）

```python
_H2_TRANSIENT_MARKERS = ("503", "no_keys_available")

def _h2_blame_gateway_flake(res: dict) -> tuple:
    blob = " ".join([str(res["raised"] or "")] + [str(r.get("err") or "") for r in res["recs"]]).lower()
    hits = [m for m in _H2_TRANSIENT_MARKERS if m in blob]
    if hits:
        return True, f"判据一命中：异常/审计文本出现 {hits}"
    five_xx = {r.get("model") for r in res["recs"] if _h2_rec_status(r) >= 500}
    cooled = set(res["cool"])
    if five_xx and cooled == five_xx:
        return True, f"判据二命中：冷却集合 {sorted(cooled)} 恰等于审计中返回 5xx 的模型集合"
    return False, (f"两条判据均不成立（文本无 {list(_H2_TRANSIENT_MARKERS)}；"
                   f"冷却集合 {sorted(cooled)} != 5xx 集合 {sorted(five_xx)}）→ 按真实缺陷定责")
```

## 3. 核心不变量（新增保护，不是放宽）— `_h2_invariant` @ 331 行

```python
    short = float(cfg["cooldown_seconds"])       # 60
    hard  = float(cfg["hard_cooldown_seconds"])  # 600
    long_cooled = {m: rem for m, rem in res["cool"].items() if rem > short}
    if long_cooled:
        print(f"     长冷却违规明细（{when}）: {long_cooled}")
    return _check(not long_cooled,
        f"{name}: [{when}] 核心不变量：没有任何模型被长冷却（>{short:.0f}s，即 hard_cooldown={hard:.0f}s）")
```

调用点 4 处（覆盖 3 条路径），**首次与重试都独立断言**：
正常路径首试（403 行）、抖动分支首试（385 行）、抖动分支重试（391 行）、
不可归因分支首试（399 行）。transient 短冷却恰好 60.0s，`> 60.0` 严格区分，不误报。

## 4. 验证（真跑原文）

### 4.1 两次连跑 `scripts\test_pool_harden.py` — 均 0 FAIL

原文存档：[W3_h2_run1.txt](W3_h2_run1.txt)、[W3_h2_run2.txt](W3_h2_run2.txt)

```
结果: 全部通过          <- run1（exit 0）
结果: 全部通过          <- run2（exit 0）；H2 两个子用例各 8 条 [PASS]，无 [FAIL]
[PASS] temperature='hot'（非法参数）: [首次] 核心不变量：没有任何模型被长冷却（>60s，即 hard_cooldown=600s）
[PASS] messages=[]（空消息）: [首次] 核心不变量：没有任何模型被长冷却（>60s，即 hard_cooldown=600s）
```

两次真跑都走了「首次即符合预期」路径（未触发重试），H1/H3/H4/H5/H6 全绿。

### 4.2 回归：`scripts\test_llm_pool.py` — 全部通过（exit 0）

原文存档：[W3_h2_test_llm_pool.txt](W3_h2_test_llm_pool.txt)

### 4.3 故障注入（离线本地桩，不打真实网关）— 20/20 通过

脚本：[_h2_fault_injection.py](_h2_fault_injection.py)；原文：[W3_h2_fault_injection.txt](W3_h2_fault_injection.txt)

| 场景 | 桩行为 | 期望 | 实测 |
|---|---|---|---|
| A flake-once | 某模型**首次** 503，重试全 400 | 重试后 PASS | 0 项未通过，桩收 6 次请求，打印「首次因网关瞬时抖动未过，重试通过」 |
| A2 对照组 | 同上，改用**改前**旧代码跑 | 复现假红 | **4 项 [FAIL]**（RequestError / 裸 Exhausted / 冷却为空 / 消息语义）— 根因复现 |
| B real-defect | 某模型 404 → 池给 **600s** | 必须 FAIL，不得重试掩盖 | **5 项 [FAIL]**，含 `长冷却违规明细（首次）: {'h2-stub-c': 600.0}`；桩只收 3 次（未重试） |
| C flake-twice | 某模型**两次**都 503 | 重试后仍 FAIL | **4 项 [FAIL]**，打印「重试仍不符合预期 → FAIL」；桩恰收 6 次 |
| D 不变量单测 | 合成冷却快照 60.0s / 599.9s | 前者不报、后者判 FAIL | 60.0s 无未通过项；599.9s 判 FAIL |

场景 B 的归因输出（证明真缺陷不会被误判成抖动）：

```
首次未完全符合预期；抖动归因判定: False —— 两条判据均不成立（文本无 ['503', 'no_keys_available']；
冷却集合 ['h2-stub-c'] != 5xx 集合 []）→ 按真实缺陷定责
```

## 5. 未动声明

- 只改 `E:\QQbot\scripts\test_pool_harden.py`（H2 区块）；H1/H3/H4/H5/H6 的断言、
  文案、执行顺序全部未动（diff hunk 范围已证明）。
- **未动**：`scripts/llm_pool.py`、`config/*`、`.env`、`reverse_bot.py`、任何 `logs\` 文件、
  NapCat/QQ 相关文件；未重启 bot、未发任何 QQ 消息。
- 本波新增文件仅在 `qq_native_re\findings\model_pool\` 与 `qq_native_re\patches\..._h2\`。
- 审计写入落在 `qq_native_re\findings\model_pool\llm_pool.jsonl`（受限进程的既有回退路径，
  非 `logs\`）；故障注入另用 `_h2_fault_injection_audit.jsonl`。
- 说明：本次把重试封装放在了 H2 共用的 `_h2_case` 里，因此 H2 的**两个**子用例
  （`temperature="hot"` 与 `messages=[]`）享有同一套抖动重试；根因机制相同，
  断言集合一字未改，未越出 H2 范围。
