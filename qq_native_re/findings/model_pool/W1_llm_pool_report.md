# 中央模型池（W1）交付报告

- 时间：2026-10-12 07:45–07:51
- 备份目录：`qq_native_re\patches\model_migration_backup_20261012-074515\`（.env 原件）
  另存变更后快照 `model_migration_backup_20261012-074556\.env.post_append`

## 路径清单（全部落盘）

| 文件 | 说明 |
|---|---|
| `scripts/llm_pool.py` | 唯一模型出口，约 700 行 |
| `config/model_pool.json` | 优先级链与参数（逐字按定稿） |
| `.env` | 追加 `LLM_POOL_BASE_URL` / `LLM_POOL_API_KEY=sk-personal-****`，未删改任何原行（14→18 行） |
| `scripts/test_llm_pool.py` | 四场景演练脚本 |
| `qq_native_re\findings\model_pool\llm_pool.jsonl` | 审计日志（21 行） |
| `qq_native_re\findings\model_pool\drill_output.txt` | 演练完整输出 |

## 四场景实测

- **A 正常**：`deepseek-flash` 回答「正常」，0.66s，落在 T1。
- **B 强制降档**：首档换成 `no-such-model-xyz` → 自动降到 **T2 `deepseek-v4-flash-ascend`**，仍拿到「正常」，
  假模型进 600s 长冷却。**未修改 config/model_pool.json**（进程内临时配置）。
- **C 全挂**：三档全假 → 抛 `LLMPoolExhausted`，消息按模型列出各自 404 原因，三个模型全部长冷却。
- **D 审计**：21 行记录，字段齐全（ts/label/tier/model/ok/latency_ms/err/tokens/final）。
- 附加验证：软开关 `LLM_POOL_DISABLE=1` → 只试 `deepseek-v4-pro` 单模型、不降档（当前它 503，如实抛错）。

**结果：全部通过（exit 0）。**

## ⚠ 需 PM 处理的一处环境限制

本机沙箱**拒绝子进程**（python/pwsh）写入 `logs\`、`config\`、`scripts\`：
`logs` 含 `Everyone:(DENY)(DC)` 且整目录不可写（连已存在的 `bot.*.log` 也不能追加）。
因此审计日志加了**自愈回退**：首选 `logs\llm_pool.jsonl`，不可写则落
`qq_native_re\findings\model_pool\llm_pool.jsonl`（可用 `LLM_POOL_LOG_PATH` 强制指定）。

关键影响：**机器人在受限沙箱内跑时，审计会写到 findings 目录而非 logs/**；若 bot 以非受限身份启动则仍写 `logs\llm_pool.jsonl`。
若 PM 要求审计必须固定在 `logs\`，需放宽该目录写入权，或统一设 `LLM_POOL_LOG_PATH`。

同一原因导致我在 `logs`/`config`/`scripts` 下无法新建文件，故本报告与产物落在 findings 目录。

## 未动其它文件声明

未改 `reverse_bot.py`、`scripts/decision_engine.py`、`scripts/model_primary.py`、`scripts/model_fallback.py`、
`config/active_model.json`，未动任何 NapCat/QQ 侧文件与进程，未引入云端兜底，未改 `scripts/test_*.py` 探针。

## 残留

`logs\_tool_probe.jsonl`(0B)、`qq_native_re\findings\model_pool\_drill_model_pool.json`(0B)、
`_drill_tmp\`(空目录) — 均为 0 字节/空目录，受限环境下无法删除，无功能影响。

## 给下一波（接入改造）的接口

```python
import llm_pool                                                  # 从 scripts/ 目录或 sys.path 含 scripts 时
text, model = llm_pool.chat_sync(messages, label="decision")      # 同步
text, model = await llm_pool.chat(messages, label="decision")     # 异步（已实测通过）
llm_pool.available_models()   # 派生 DecisionEngine.AVAILABLE_MODELS
```

签名（已实测打印）：`chat` / `chat_sync` 均为
`(messages, *, label='general', max_tokens=None, temperature=None, response_format=None, preferred=None, timeout=None) -> tuple[str, str]`。

- `available_models()` 返回 8 个模型的优先级序列表，改 `config/model_pool.json` 即生效（mtime 缓存，无需重启）。
- **import 注意**：`llm_pool.py` 位于 `scripts/`。从项目根执行 `python -c "import llm_pool"` 会
  `ModuleNotFoundError`；要在 `scripts/` 目录内执行，或先 `sys.path.insert(0, "scripts")`。
  若让 `decision_engine.py` 用 `from scripts.llm_pool import ...` 也可（两者 import 路径都被支持）。
- `llm_pool` 自己会 `load_dotenv(项目根/.env, override=False)`，因此子进程
  （`model_primary.py` / `model_fallback.py`）即便不继承环境也能拿到 key。
- 单次尝试 20s、总预算 45s，**小于 decision_engine 子进程 60s 超时**，留有余量。

## 附加实测（07:52）

- 异步入口 `await llm_pool.chat(...)` → `deepseek-flash` / 「正常」，通过。
- `LLMPoolExhausted` 确为 `RuntimeError` 子类，便于调用点按既有异常处理风格捕获。
- `py_compile` 语法检查通过；`scripts/__pycache__` 不可写但 import 正常（仅少一层缓存）。
- 三个产出文件 grep 确认：**0 处** `api.llm.ustc` / `api.deepseek` 残留。

`active_model.json` 仍是 `deepseek-v4-pro`（本次未改，留给接入波次按方案改 `deepseek-flash`）。
