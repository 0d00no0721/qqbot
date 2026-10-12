# 决策日志 007 — 模型调用迁移至本地网关（含优先级故障转移）与群 284517232 启用

> 日期：2026-10-12 08:40
> 决策者：PM
> 状态：**已上线并实证**（用户重启 bot 后真实群流量验证通过）
> 依据：findings/model_pool/（PROBE.md、W1/W1b/W2/W3 各报告、drill 输出、审计日志）、logs/bot.2026-10-12.log

## 一、做了什么

1. 新增唯一模型出口 `scripts/llm_pool.py`（1100 行）：按优先级链故障转移 + 冷却（普通 60s / 模型不存在 600s）+ 总预算（单次 20s / 总 45s，严格小于 decision_engine 子进程 60s 超时）+ 审计 JSONL + 软开关 `LLM_POOL_DISABLE=1`。
2. 优先级链（`config/model_pool.json`，用户定稿）：T1[deepseek-flash, deepseek-flash-2] → T2[deepseek-v4-flash-ascend, deepseek-v4-flash-ascend1] → T3[qwen3.8-flash-next-2, qwen3.8-flash-next] → T4[glm-5.3-flash] → T5[qwen3.8-reasoner, qwen-reasoner]（档内同优先级、按序试；某档全挂才降档）。
3. 全部调用点接线（双路兼容 import，避开包式/脚本式导入差异）：主聊天链路（`decision_engine.py` + `model_primary.py` / `model_fallback.py` + `config/active_model.json`）与 7 个辅助调用点（画像 user_profile、记忆分类 memory_manager、周礼 zhouli、算卦 divination、_helpers、_state、系统测试命令 system）。
4. 端点与密钥：`.env` 新增 `LLM_POOL_BASE_URL=http://127.0.0.1:8090/v1` 与 `LLM_POOL_API_KEY`；旧三个云端 key（DEEPSEEK/DECISION/DIVINATION）**保留未删**，作回滚锚点。
5. **不保留云端兜底**（用户决定）：全部模型调用只走本地网关。
6. 群 284517232 启用：五项功能全开（决策引擎/指令/+1/新人欢迎/画像）+ 显式角色绑定 `soyo nagasaki`；群名填为「碧油鸡乐土（转正版）」。

## 二、上线实证（2026-10-12 08:16–08:26，bot 重启后真实群流量）

- 进程与链路：`netstat -ano` → `0.0.0.0:8080 LISTENING`（PID 8272）+ `127.0.0.1:8080 ↔ 127.0.0.1:52986` ESTABLISHED ⇒ NapCat 反向 WS 已自动重连，**未重扫码**。
- bot 日志 `logs\bot.2026-10-12.log`：08:16:24 / 08:18:38 / 08:24:19 三次 `_call_model_script: script=model_primary.py, gid=284517232` → `[model_primary] 调用成功: 命中模型=deepseek-flash` → `[熔断] 主模型回复有效` → `[决策] 群 284517232 回复`。
- 审计 `logs\llm_pool.jsonl`（重启后自动从 findings 切回 logs，自愈逻辑按预期工作）：3 行，均 `tier=preferred, model=deepseek-flash, ok=true`，延迟 467–771ms，tokens ≈3700。
- 结论：**新启用的群 284517232 首次真正被 bot 接话，且回复由本地网关 deepseek-flash 生成**；故障转移链未被触发（正常态）。
- 迁移前基线佐证：裸命令 `import reverse_bot` → OK / EXIT=0（日志守卫生效）；被改运行时文件 grep 云端端点零残留。

## 三、过程中的裁决记录

1. **T5 名称更正**：PM 最初依据 `/v1/models` 判定 `qwen-reasoner` 不存在、建议映射为 `qwen3.8-reasoner`；探针实测（PM 亲自复核）证明该模型**列表外但可调通**（HTTP 200、content 非空、1.3–1.6s）⇒ 恢复用户原始清单 T5=[qwen3.8-reasoner, qwen-reasoner]。**教训：配置即真相，不要用 /v1/models 做白名单过滤。**
2. **400 分流判据**：PM 任务书假设「参数错 = error.type 为 litellm.BadRequestError」有误；实测两种 400 的 `error.type` 都是 `invalid_request_error`（差别在 message）⇒ 实现改为 type+message 双判据。
3. **glm-5.3-flash 的 `model_overrides.min_max_tokens=128`** 被全局 `min_max_tokens=512` 覆盖（实现为 `max(全局, 覆盖)`，覆盖只能抬高）：刻意保留，防止思考型模型「思考未写完 ⇒ content 为空」的假故障复发。
4. **加固必要性实证**：现网调用点存在 `max_tokens=10/100/150/200`（system.py、character.py、memory_manager.py 等）；若不做 max_tokens 下限，T3/T4/T5 会 100% 假故障——故障转移将形同虚设。
5. **401 止损**：遇 401 auth_error 立即中止整池尝试（不逐档重试、不设冷却），抛 `LLMPoolAuthError`；新异常均继承 `LLMPoolExhausted`，保证下游 except 不漏捕。
6. **日志守卫**：`decision_engine.py` 与 `reverse_bot.py` 的模块级 FileHandler 均加 `try/except OSError` 降级（受限环境不再阻断 import；logs 可写时行为逐字不变）。

## 四、回滚

- 改前原件备份：`qq_native_re/patches/model_migration_backup_20261012-*`（含 .env、decision_engine、model_primary、model_fallback、active_model、user_profile、memory_manager、zhouli、divination、_helpers、_state、system、reverse_bot、decision_rules、group_features、group_characters、test_pool_harden 等）。
- 软开关：`LLM_POOL_DISABLE=1` ⇒ 只调 active_model 单模型、不降档（用于定位「是不是池的问题」）。
- 完整回滚：按上述备份还原文件 + 由用户在控制台重启 bot（**不得由沙箱内进程启动**，见 §五.5）。

## 五、遗留待办（不影响运行）

1. `scripts/test_pool_harden.py` 的 H2 子用例依赖真实网关，偶发假红（网关对单个模型瞬时 503 ⇒ 池按 transient 短冷却处理，行为正确，是测试假设过严）→ 后续改为「断言不变量 + 必要时重试一次」。
2. 惰性云端字面量残留（均不在运行链路）：`scripts/reply_generator.py`（全仓 0 引用死代码）、`scripts/test_fallback_api.py`、`scripts/test_subprocess_call.py`（手工调试脚本）。
3. `logs\` 目录 ACL 异常（`Everyone:(DENY)(DC)`）：用户决定暂不修；已用日志守卫降级 + 审计自愈绕开；副作用是受限进程无法写 logs/。
4. NapCat 残留 Lagrange 适配器（`napcat_protocol_2668851638.json` 指向旧路径）：与踢下线观察窗无冲突，留待观察窗后随 NapCat 升级一并清理。
5. **环境铁律**：沙箱内 `asyncio.create_subprocess_exec` 必 WinError 5，且受限进程无法写 logs/config/scripts/ ⇒ **bot 必须由用户在普通控制台启动**，否则会「在线但永远不回复」。

## 六、运维速查

- 改优先级链：编辑 `config/model_pool.json` 的 `tiers`（mtime 热加载，无需重启）；改链后同步更新 `scripts/test_pool_harden.py` 的 `EXPECTED_TIERS`。
- 切换主模型：改 `config/active_model.json`（或机器人指令），模型必须在链内。
- 看谁在扛 / 谁常挂：`logs\llm_pool.jsonl`（受限环境回退 `qq_native_re\findings\model_pool\llm_pool.jsonl`）。
- 群功能开关：`config/group_features.json`（热加载）或 `python scripts\group_manager.py`。

## 七、当前生效状态

| 项 | 值 |
|---|---|
| 模型出口 | `http://127.0.0.1:8090/v1`（唯一） |
| 主模型 | deepseek-flash |
| 优先级链 | 5 档 9 模型（见 §一.2） |
| 云端兜底 | 无（用户决定） |
| 启用群 | 755471390（全开）、**284517232（全开，本次启用）**；1055881417 保持关闭 |
| 踢下线观察窗 | 进行中：t0=2026-10-12 07:28，48h 判读点 10-14 07:30（与本次迁移无关，未受影响） |

## 附：原始证据（PM 亲自取证）

```
netstat -ano | Select-String ':8080'
  TCP    0.0.0.0:8080           0.0.0.0:0              LISTENING       8272
  TCP    127.0.0.1:8080         127.0.0.1:52986        ESTABLISHED     8272
  TCP    127.0.0.1:52986        127.0.0.1:8080         ESTABLISHED     580

get-content logs\bot.2026-10-12.log -Tail
  [2026-10-12 08:16:24] [熔断] INFO: [调试] _call_model_script 被调用: script=model_primary.py, gid=284517232
  [2026-10-12 08:16:37] [熔断] ERROR: 模型脚本 model_primary.py stderr: [model_primary] 调用成功: 命中模型=deepseek-flash
  [2026-10-12 08:16:37] [熔断] INFO: [熔断] 主模型回复有效
  [2026-10-12 08:16:37] [决策] 群 284517232 回复（总耗时 13319ms）
  （08:18:38、08:24:19 另两次同型）

logs\llm_pool.jsonl（全部 3 行）
  {"ts": "2026-10-12T08:16:37+08:00", "label": "model_primary", "tier": "preferred", "model": "deepseek-flash", "ok": true, "latency_ms": 771, "tokens": 3704, "final": true}
  {"ts": "2026-10-12T08:18:39+08:00", ... "latency_ms": 645, "tokens": 3729 ...}
  {"ts": "2026-10-12T08:24:20+08:00", ... "latency_ms": 467, "tokens": 3732 ...}
```

---
*创建：2026-10-12 08:40（decision-007）。配套：findings/model_pool/ 全部报告、patches/model_migration_backup_20261012-* 备份集*
