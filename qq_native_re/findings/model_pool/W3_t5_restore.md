# W3 — T5 恢复为用户原始清单

**改动（仅 1 行，`config/model_pool.json`）**
改前 T5：`["qwen3.8-reasoner"]` → 改后 T5：`["qwen3.8-reasoner", "qwen-reasoner"]`
Compare-Object 仅此 1 行差异；11 个配置键、2 空格缩进、LF、末尾换行逐字保留（JSON 解析通过）。

**备份**：`qq_native_re\patches\model_migration_backup_20261012-080805_t5\model_pool.json`（612 B，07:58:25 版）

**实证**（原文：`qq_native_re\findings\model_pool\drill_output_t5.txt`）
- a) **PASS** — 9 项，末两项 `qwen3.8-reasoner, qwen-reasoner`
- b) **PASS** — `命中= qwen-reasoner | 内容= '正常'`（经池调用，内容非空）
- c) **PASS** — 8 PASS / 0 FAIL，结论「全部通过」
- d) **79 PASS / 2 FAIL** ← 见下

**⚠ 标红：d) 的 2 项 FAIL 是基线失配，不是功能回归**
H6 基准取自 W1 备份模块的硬编码 `_FALLBACK_CONFIG`（备份位于 `patches\` 下，其 CONFIG_PATH 解析不到文件 → 走硬编码），而该基准 tiers 与**改前**文件逐字相同（反证：相等 = True）。
反证差异：`old-only=[]`（无删除）、`new-only=['qwen-reasoner']`、差异档位 index=4（即 T5）。故 2 项 FAIL 恰等于本次唯一改动；其余 79 项（签名、异常继承、审计字段、其余配置键、降档、400 分流、401 止损、max_tokens 下限、报文捕获）全过。
**请 PM 裁决**：更新 `test_pool_harden.py` 的 H6 基线为「T5 含 qwen-reasoner」，或接受该基线失配。我未改测试脚本（红线）。

**未动声明**：.env、`scripts/llm_pool.py`、`scripts/test_*.py`、其它档位，以及 min_max_tokens / model_overrides / no_response_format / 超时与冷却参数均未改；未重启 bot、未碰 NapCat/QQ。key 脱敏 `sk-personal-****`。
