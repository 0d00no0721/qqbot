# W3 测试基线修正：H6 改为与显式 EXPECTED_TIERS 比对

**改了什么（仅 `scripts/test_pool_harden.py`）**
- L44~55 新增 `EXPECTED_TIERS`（5 档 9 模型）+ 中文注释「业务有意变更时必须同步更新此常量」。
- L699~723 H6 配置段：删掉 `cfg_old = old._FALLBACK_CONFIG`（不再引用备份硬编码基准），改为断言
  ①`cfg['tiers'] == EXPECTED_TIERS` ②`available_models()` == EXPECTED_TIERS 展平去重序列 ③无重复模型。
- H6 其余项（8 个签名、异常继承/新增性、审计 8 字段、5 个既有配置键）与 H1~H5 **一行未动、未放宽**。

**三组跑分**
| 场景 | 结果 |
|---|---|
| 正常（`_run_h6_normal.txt`）| **82 PASS / 0 FAIL，exit 0** |
| 误改 T5→`["qwen3.8-reasoner"]`（`_run_h6_misconfig.txt`）| **3 FAIL / 79 PASS，exit 1**，且**只有 H6 变红** |
| 恢复后复跑（`_run_h6_restored2.txt`）| **82 PASS / 0 FAIL，exit 0** |

误改态原文：`[FAIL] 优先级链 tiers 与 EXPECTED_TIERS 逐档一致（含顺序与成员）` /
`[FAIL] available_models() 与 EXPECTED_TIERS 展平去重序列逐项一致（9 个）`。

**反向验证结论**：H6 仍能精确捕获档位误改（`new-only=['qwen-reasoner']`），不是「永远通过」的假关卡。

**W1 无回归**：`test_llm_pool.py` A/B/C/D 四场景全过（8 PASS / 0 FAIL，exit 0）。

**环境抖动留档（非本次引入）**：另一次跑分（`_run_h6_restored.txt`）H6 全绿但 H2 `temperature='hot'` 4 FAIL，
审计证实上游对 `glm-5.3-flash` 瞬时返回 `HTTP 503/no_keys_available`（其余 8 模型如期 400），
池按设计耗尽成 `LLMPoolExhausted`。属 H2 依赖真实网关的固有抖动，**按约束未放宽 H1~H5**，仅留档。

**备份**：`qq_native_re\patches\model_migration_backup_20261012-081044_baseline\test_pool_harden.py`（改前原文 `BC2C320F…`）

**未动其它文件**：`scripts/llm_pool.py`（mtime 7:59:43 / `C6094E1B…`）、`config/model_pool.json`（`252E4DB4…`，tiers 亲读复核为 5 档 9 模型）、
`scripts/test_llm_pool.py`（`4D92CDE6…`）、任何运行时调用点；未碰 NapCat/QQ、未重启 bot。
