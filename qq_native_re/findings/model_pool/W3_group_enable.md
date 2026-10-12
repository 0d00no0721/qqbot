# W3 — 启用 QQ 群 284517232 机器人功能

**状态**：✅ 完成（24/24 校验 PASS，exit=0）
**执行时间**：2026-10-12 08:12–08:15
**备份目录**：`E:\QQbot\qq_native_re\patches\model_migration_backup_20261012-081241_group\`
（含改动前的 `group_features.json` 668B / `group_characters.json` 34B）

## 1. 改动内容

**① `config/group_features.json`** — 仅 `284517232` 条目：`group_name` 由 `""` 改为 `"碧油鸡乐土（转正版）"`，五项 features 由全 `false` 改为全 `true`（`decision_engine` / `plus_one` / `commands` / `newcomer_welcome` / `profile_record`）。`Compare-Object` 显示全文件仅这 6 行发生变化。

**② `config/group_characters.json`** — 新增一行 `"284517232": "soyo nagasaki"`（原 `755471390` 行末补逗号），与参照组显式绑定一致。

## 2. 验证（真跑 `_w3_group_check.py`，可复跑）

脚本直接 `import reverse_bot` 调用**真实** `_feature_on`（非复刻逻辑；`websockets.serve` 在 `__main__` 内，import 不占端口），并走 `DecisionEngine.get_character_for_group`。关键输出：

```
[PASS] group_features.json json.load 通过 -> keys=['284517232','1055881417','755471390']
[PASS] group_characters.json json.load 通过 -> {"755471390": "soyo nagasaki", "284517232": "soyo nagasaki"}
[PASS] 使用的是 reverse_bot 真实函数 -> src=E:\QQbot\reverse_bot.py
[PASS] 284517232 五项 == True  -> {decision_engine,plus_one,commands,newcomer_welcome,profile_record 全 true}
[PASS] 1055881417 五项 == False
[PASS] 755471390 五项 == True
[PASS] L247 any() 闸门: 284517232 => 消息不再被丢弃  -> PASS_THROUGH=True
[PASS] L247 any() 闸门: 1055881417 => 消息仍被丢弃  -> PASS_THROUGH=False
[PASS] 284517232 解析角色 == 'soyo nagasaki'
[PASS] 角色目录存在（isdir 前置条件） -> E:\QQbot\config\characters\soyo nagasaki
[PASS] 未写入 group_features.json  -> 11553b44e76657be
[PASS] 未写入 group_characters.json -> eb2ee3e0dbbafed4
RESULT: ALL PASS   (FAIL=0)
```

运行中出现的 `无法写入文件日志 logs\bot.*.log（权限不足），已降级为仅控制台日志` 是沙箱内 import 的预期降级，与本次改动无关。

## 3. 另两群零差异证明

| 群 | 深度相等 | 逐字序列化一致 |
|---|---|---|
| `1055881417` | ✅ 与备份 `{"features":{全 false},"group_name":""}` 一致 | ✅ |
| `755471390` | ✅ 与备份 `{"features":{全 true},"group_name":""}` 一致 | ✅ |

另：`group_features.json` 群键集合与备份完全相同；`group_characters.json` 相对备份新增键集合恰为 `{'284517232'}`；`755471390` 映射值不变。

## 4. 声明

- **未改任何 .py 与其它配置**。本会话时间窗（08:12 后）内我仅写盘 4 个文件：上述两个 JSON、本报告、验证脚本。
- `config/active_character.json` 未改动（mtime 仍 2026/7/10 10:00:44，取证值 `enabled=true / soyo nagasaki`）。
- **未重启 bot、未触碰 NapCat/QQ**。验证脚本全程只读，首尾 sha256 一致。
- 观察（非本任务改动）：`reverse_bot.py`(08:08:14) 与 `scripts/decision_engine.py`(08:08:01) 的 mtime 早于本会话开始（08:12:36），系他人/前序改动；本次验证读的是这两个当前版本。
- 观察：bot 仍在运行 —— `0.0.0.0:8080 LISTENING`(PID 29740)，且有 NapCat 的 ESTABLISHED 连接。注意 `Get-NetTCPConnection` 在本沙箱下返回空（假阴性），`netstat -ano` 为准。
- 群功能文件是 mtime 热加载，无需重启即生效（下条消息到达时按新配置读取）；用户随后重启一次亦为稳妥。
