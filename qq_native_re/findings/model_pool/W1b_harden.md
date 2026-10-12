# W1b —— 中央模型池加固补丁报告

> 执行者：harden 子 agent（PM 委派）　| 完成：2026-10-12 08:07 (+08:00)　| 项目根：`E:\QQbot`
> 改动文件：**`scripts/llm_pool.py`**、**`config/model_pool.json`**（+ 新增演练脚本 `scripts/test_pool_harden.py`）
> 备份：`qq_native_re/patches/model_migration_backup_20261012-075546_harden/`（`llm_pool.py` 25916B + `model_pool.json` 454B，补丁前原件）
> 证据：`qq_native_re/findings/model_pool/drill_output_harden.txt`（369 行 / 34166 B，**89 项 PASS / 0 项 FAIL**）
> Key 一律脱敏为 `sk-personal-****`；上游 key 持有人姓名一律脱敏为 `<上游key-N>`

---

## 0. 一句话结论

**七个加固点全部落地、真机演练全过、W1 四场景一字未改且仍全过、接口与审计字段向后兼容有自动化比对证明。**
过程中发现两处**任务书假设与实测不符**（见 §3），已按"不能让故障转移失效"的原则处置，请 PM 过目裁决。

| # | 加固项 | 状态 | 关键证据 |
|---|---|---|---|
| 1 | max_tokens 全局下限 512 + per-model 覆盖 | ✅ | H1/H4；raw mt=16 空内容反证 |
| 2 | 400 按语义分流（请求侧不冷却） | ✅ | H2/H5；8 模型全 400 后冷却仍为空 |
| 3 | 401 立即止损 | ✅ | H3；只尝试 1 次、0.28s、无冷却 |
| 4 | 审计 err 瘦身 | ✅ | R5 前后对比；补丁后泄露行 0 条 |
| 5 | qwen3.8-flash-next 禁用 response_format | ✅ | H4 报文捕获（wire 级证明） |
| 6 | content strip / finish_reason 保留确认 | ✅ | 代码确认 + 空内容消息增强 |
| 7 | 新异常继承 LLMPoolExhausted + 兼容 | ✅ | H6 逐项比对（签名/字段/链/配置键） |

---

## 1. 逐项改动（怎么改的 + 证据）

### 1.1 max_tokens 全局下限（最高优先级）

- `config/model_pool.json` 新增：
  - `"min_max_tokens": 512`（全局安全网）
  - `"model_overrides": {"glm-5.3-flash": {"min_max_tokens": 128}}`（结构可扩展，后续可加别的键）
- `llm_pool.py` 新增公开辅助 `model_min_max_tokens(model)` / `effective_max_tokens(model, requested)`；
  `chat()` 里 `payload["max_tokens"] = effective_max_tokens(model, max_tokens)`（**逐模型**计算）。
- 语义：`实际值 = max(调用方值, 该模型下限)`；调用方没给时用 `max_tokens_default`(500) 作诉求值 →
  对 T3/T4/T5 **实际生效 512**（即 `max_tokens_default` 的 500 被安全网抬到 512）。
- **取证（H1）**：绕过池直连网关发 `max_tokens=16` → `HTTP 200 content='' finish_reason=length`（复现 R1）；
  池调用同样传 16（首档=不存在的模型，第二档=`qwen3.8-flash-next-2`）→ 拿到 `'正常'`、0.78–1.88s、
  该模型**未被冷却**，不存在的首档照旧长冷却 598.6s。
- **取证（H4 本地桩）**：`qwen3.8-flash-next` 与 `deepseek-flash` 的 wire 报文里 `max_tokens` 均为 **512**（调用方传 16）。

> 现场意义：现网调用点确实在传小值 —— `scripts/commands/system.py:92` **max_tokens=10**、
> `scripts/commands/character.py:24` **100**、`scripts/memory_manager.py:123` **100**、
> `scripts/commands/_helpers.py:14` 默认 **150**、`scripts/reply_generator.py:71` **200**。
> 这些路径一旦被 W2 接进池，没有下限就是 100% 空内容 → 误判故障 + 冷却。（当前生产代码里
> `llm_pool.chat/chat_sync` 的调用点还只有演练脚本，W2 正在接线。）

### 1.2 400 按语义分流，禁止一刀切

- 新增 `_parse_error()`（只取顶层 `error.type` / `error.message`）+ `_classify_http_error()`，把非 200 分成四类：
  | 分流 | 触发 | 处置 |
  |---|---|---|
  | `auth` | 401；403 且鉴权语义 | 立即中止整池、不冷却 → `LLMPoolAuthError` |
  | `request` | 参数/校验类 400（`litellm.BadRequestError` 等） | **不冷却**、换下一个；全部中标 → `LLMPoolRequestError` |
  | `model_missing` | 404；400 `Invalid model name`；按模型的 403 | 长冷却 600s、换下一个 |
  | `transient` | 429 / 5xx / 其它 | 短冷却 60s、换下一个 |
- `chat()` 用 `attempts[]` 记录每次尝试的 `kind`，收尾时 `all(kind == "request")` → 抛 `LLMPoolRequestError`
  （消息含「请求本身有问题，不是模型故障」）。
- **取证（H2）**：`temperature="hot"` 与 `messages=[]` 两个子用例，各试满 **8 个模型** →
  均抛 `LLMPoolRequestError`，并且 `cooldown_snapshot() == {}`（**整池没被冻结**），耗时各 1.1s。
- 另修一处会导致本项失效的隐患：`temperature` 原本 `float(temperature)` 直接抛 `ValueError`（本地崩，
  绕过分流）；现在非法值**原样透传**给网关，由网关 400 走 `request` 分流。

### 1.3 401 立即止损

- 循环内命中 `auth` → `_audit` 记一行后**立刻 `raise LLMPoolAuthError`**，不设冷却、不试下一档。
- 消息：`网关鉴权失败，检查 .env 的 LLM_POOL_API_KEY（或 config/model_pool.json 的 api_key）。HTTP 401/auth_error: invalid token —— 鉴权类错误是全局故障，已立即中止整池尝试：不降档、不设冷却。`
- **取证（H3）**：进程内把 `LLM_POOL_API_KEY` 覆盖为 `sk-drill-badkey-h3`（**.env 未改动**，
  结束后已还原并清缓存）；坏 key 先直连确认 `HTTP 401 {"error":{"message":"invalid token","type":"auth_error"}}`；
  池调用 → 抛 `LLMPoolAuthError`、**审计尝试次数 = 1**（池内 8 个模型，若逐档重试会有 8 次）、
  0.28s、冷却快照为空。

### 1.4 审计 err 瘦身

- 新增 `_sanitize()`（折叠空白 + `sk-*` 一律打码 + 截断）与 `_slim_err()`：
  `err = "HTTP <status>/<error.type>: <message 截断 200 字>"`（模型不存在时补 `[长冷却 600s]` 注记）。
- 绝不再把响应体原样写进日志：只取顶层 `error.type`/`error.message`，**不碰 `attempts[]`**。
- **取证**：`qwen-reasoner-typo` 的 404 body 368–646 字符、含 `attempts[].key` →
  旧格式 err 231 字符且**含上游 key 持有人姓名**；新格式 **56 字符、零内部名**。
- 全局统计：补丁后新增审计行中含上游 key 名的 **0 条**；最长 err **232 字符**（≈200 字消息 + 前缀）。
  补丁前的 **13 条历史泄露行原样保留**（属 PM 证据，见 §5 遗留）。

### 1.5 per-model 能力开关（response_format）

- config 新增 `"no_response_format": ["qwen3.8-flash-next"]`；`supports_response_format(model)` 判定，
  池调用该模型时不带 `response_format`，其余模型照常传。
- **取证（H4）**：本地捕获桩实测 wire 报文 ——
  `qwen3.8-flash-next` → `{"model","messages","max_tokens":512,"temperature":0.2}`（**无** response_format）；
  `deepseek-flash` → 同上 **+ `"response_format":{"type":"json_object"}`**。

### 1.6 content strip / 空内容诊断（确认保留 + 小增强）

- `_extract_text()` 仍 `str(content).strip()`（T3/T5 的 `'\n\n正常'` 已被剥掉，H1 拿到的就是 `'正常'`）；
- 空内容错误消息仍带 `finish_reason`，并**追加** `text_tokens` / `reasoning_tokens`
  （PROBE §5.2 提醒不能只看 `finish_reason=="length"`，例如 `qwen3.8-flash-next` 走 response_format 时是 `stop`）。

### 1.7 向后兼容

- 新增异常均为 `LLMPoolExhausted` 子类：`class LLMPoolRequestError(LLMPoolExhausted)`、`class LLMPoolAuthError(LLMPoolExhausted)`。
- **取证（H6，自动化逐项比对补丁前备份）**：
  - 8 个公开函数签名与 W1 **逐字一致**（chat / chat_sync / available_models / load_config /
    cooldown_snapshot / clear_cooldowns / audit_log_path / get_active_model）；
  - 审计字段仍是 `ts,label,tier,model,ok,latency_ms,err,tokens`(+可选 `final`)，无计划外字段；
  - `available_models()` 与 W1 完全一致（8 个）、`tiers` 顺序/成员未变、5 个既有配置键仍在；
  - 两个新异常在 W1 里不存在（确认是新增而非改名）。

---

## 2. 演练结果

| 演练 | 命令 | 结果 |
|---|---|---|
| W1 四场景 A/B/C/D | `python scripts\test_llm_pool.py` | **全部通过**（A 落 T1、B 降档拿到回复、C 抛 Exhausted 带明细、D 审计正常） |
| H1 小 max_tokens 降档 | `python scripts\test_pool_harden.py` | 全部通过 |
| H2 请求侧错误不冻结 | 同上 | 全部通过（2 个子用例） |
| H3 401 立即止损 | 同上 | 全部通过 |
| H4 wire 报文捕获 | 同上 | done，全部通过 |
| H5 错误分流表（3 真机 body + 9 样本） | 同上 | 12 条全部按预期分流 |
| H6 向后兼容自检 | 同上 | 全部通过 |
| py_compile | `python qq_native_re\findings\model_pool\_pycompile_check.py` | `PY_COMPILE_OK`（3 个文件） |
| 软开关回归 | `LLM_POOL_DISABLE=1 python _disable_check.py` | [1] 正常 [2] 请求错→RequestError [3] 按设计忽略 tiers [4] 冷却中仍抛普通 Exhausted |

**演练脚本选择说明**：新增 `scripts/test_pool_harden.py`，**没有改动 W1 的 `scripts/test_llm_pool.py`**
（其 mtime 仍是 W1 的 07:49:37），这样"四场景仍全过"是在**原封不动的 W1 验收脚本**上取得的。

> 环境备注：`python -m py_compile scripts\llm_pool.py` 会因本机 `scripts/__pycache__` 目录 ACL 不可写报
> `Errno 13`；改用显式 `cfile` 落到 findings 目录后通过。属环境 ACL 问题，与代码无关（未去动 ACL）。

---

## 3. ⚠️ 与任务书假设不符的两处实测事实（需 PM 知悉/裁决）

1. **「参数错 → 400 `type=litellm.BadRequestError`」实测不成立。**
   真机两种 400 **都**返回 `error.type="invalid_request_error"`，唯一区别在 `error.message`：
   参数错是 `litellm.BadRequestError: OpenAIException - Failed to deserialize / Validation: ...`，
   模型名错是 `Invalid model name passed in model=`。
   → 若严格"按 error.type 分流"，这两种 400 无法区分，**本项修复会静默失效**（照样一刀切）。
   实现改为 **error.type + message 关键词双判据**，H5 对两种真机 body 分别验证为 `request` / `model_missing`。

2. **`model_overrides` 里 glm 的 128 被全局 512 覆盖（刻意）。**
   实现为 `max(全局下限, per-model 覆盖)` —— 覆盖**只允许抬高**、不允许低于全局安全网。
   理由：PROBE §5.3 明确建议全局 512 覆盖所有思考型模型并留 4–8× 余量；若让 glm 落到 128，
   中等长度提示词又会踩回 R1 的"空内容 → 误判故障"。
   若 PM 要 glm 单独用 128（例如为省时延），只需把 `model_min_max_tokens()` 里的 `max(...)`
   改成"覆盖优先"一行即可 —— **请裁决**。其余 7 个模型不受影响（都是 512）。

3. 另两处按"保故障转移可靠"原则的处置，供知悉：
   - `403` 分两种语义：`key not allowed to access model`（按模型的能力限制，**正是 404 的成因**）
     → 长冷却；真鉴权语义（`invalid token`/`auth_error`）→ 立即中止。若把前者当全局鉴权故障，一次
     单模型 403 就会废掉整条降档链。
   - 未知 400（解析不出类型）一律按 `request` 处理 → 宁可少冷却，也不让一次坏请求冻死整池。

---

## 4. 未改动清单（红线声明）

- **未改** `scripts/test_llm_pool.py`、任何调用点脚本（`decision_engine.py` / `model_primary.py` /
  `model_fallback.py` / `commands/*` / `memory_manager.py` 等 —— 它们 07:57–07:58 的改动属**并行的 W2 worker**，不是我）、
  `.env`（H3 只在内存里覆盖并已还原）、`reverse_bot.py`、NapCat/QQ、`logs/` ACL。
- **未改** `tiers` 顺序与成员、未增删模型（`smart/default`、`qwen-reasoner` 是否入链仍待 PM 定）。
- 本次实际写入的文件只有 4 类：`scripts/llm_pool.py`、`config/model_pool.json`、
  新增 `scripts/test_pool_harden.py`、`qq_native_re/findings/model_pool/` 下的报告/演练脚本/输出。

---

## 5. 遗留与建议（不在本次 7 条范围内，未擅自改）

1. **`llm_pool.jsonl` 里 13 条补丁前历史行仍含上游 key 持有人姓名**（07:49:41–07:49:52 的 drill-B/C）。
   属 PM 证据、我未改动。建议：由 PM 决定是否单独脱敏归档（同样的名字还出现在 `probe_results.json` 与
   W1 的 `drill_output.txt`）。
2. **429 仍是 60s 短冷却**（PROBE §5.1 建议 600s：限流属配额问题）。未改，避免超出本次范围。
3. **200 但内容为空仍是 60s 冷却**（PROBE §5.1 建议"不冷却"，因为本质是参数问题）。现在有 512 下限兜底，
   触发概率已大幅下降；是否改为不冷却请 PM 定。
4. **未配置 api_key 时仍抛普通 `LLMPoolExhausted`**（未改成 `LLMPoolAuthError`），保持 W1 行为不变。
5. R2 时延风险（`glm-5.3-flash` 长输入 15s、mt=4096 时 33.5s 超 20s 单次超时）与 R6/R7 选型事项未处理。

---

## 6. 复现命令

```powershell
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" scripts\test_llm_pool.py          # W1 四场景（未改）
& ".\venv\Scripts\python" scripts\test_pool_harden.py       # W1b H1–H6
& ".\venv\Scripts\python" qq_native_re\findings\model_pool\_pycompile_check.py
& ".\venv\Scripts\python" qq_native_re\findings\model_pool\_r5_check.py         # 审计瘦身前面对比
$env:LLM_POOL_DISABLE="1"; & ".\venv\Scripts\python" qq_native_re\findings\model_pool\_disable_check.py
& ".\venv\Scripts\python" qq_native_re\findings\model_pool\_assemble_drill_output.py  # 重新汇总证据
```

## 7. 证据文件清单

| 文件 | 内容 |
|---|---|
| `qq_native_re/findings/model_pool/drill_output_harden.txt` | **主证据**：6 节完整演练输出（89 PASS / 0 FAIL） |
| `qq_native_re/findings/model_pool/W1b_harden.md` | 本报告 |
| `qq_native_re/patches/model_migration_backup_20261012-075546_harden/` | 补丁前原件备份 |
| `scripts/test_pool_harden.py` | 新增演练脚本（H1–H6，可重复执行） |
| `qq_native_re/findings/model_pool/_pycompile_check.py` / `_r5_check.py` / `_disable_check.py` / `_assemble_drill_output.py` | 取证脚本（保留以便复现） |
| `qq_native_re/findings/model_pool/llm_pool.jsonl` | 审计日志（补丁后新增行 0 条含内部名） |
