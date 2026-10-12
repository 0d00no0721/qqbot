# W2 辅助调用点接线报告（其余全部模型调用点 → scripts/llm_pool.py）

- 时间：2026-10-12 07:55 – 08:10
- 执行者：W2 实现子 agent
- 备份目录：`E:\QQbot\qq_native_re\patches\model_migration_backup_20261012-075552\`（9 个文件原件）
- 唯一出口：`http://127.0.0.1:8090/v1`（key 一律脱敏 `sk-personal-****`）
- 硬约束：① 双路兼容 import ② 被改文件零云端端点残留 ③ 对外签名/返回结构/异常风格不变 ④ 未动 `config/model_pool.json` 优先级链
- **结论：改 7 个文件，冒烟 9/9 PASS（exit 0），零云端残留，import 链完整，bot 未重启、NapCat 未触碰。**

---

## 1. 逐文件改动记录

### 1.1 `scripts/commands/_state.py`（常量层）
- 新增双路兼容 import：`chat / chat_sync / available_models / load_config / get_active_model / LLMPoolExhausted`（`try: from scripts.llm_pool import ... except ImportError: from llm_pool import ...`）。
- 新增只读快照：`_POOL_BASE_URL`（读池配置，兜底 `http://127.0.0.1:8090/v1`）、`_POOL_API_KEY`、`_POOL_MODELS`、`_POOL_DEFAULT_MODEL`（active_model 在池内则用它，否则用链首 `deepseek-flash`）。
- 旧名全部保留、语义改为本地网关（其它模块 import 不报错）：
  `DEEPSEEK_API_KEY=池 key`、`DEEPSEEK_BASE_URL=http://127.0.0.1:8090`、`DEEPSEEK_API_BASE=http://127.0.0.1:8090/v1`、`DEEPSEEK_MODEL=deepseek-flash`、`DIVINATION_API_KEY=池 key`、`DIVINATION_MODEL=deepseek-flash`。
- 常量层整体 try/except 包裹：配置异常也绝不阻断 import（否则 bot 起不来）。
- 自检：`BASE= http://127.0.0.1:8090 / API_BASE= .../v1 / MODEL= deepseek-flash / KEY_SET= True`。

### 1.2 `scripts/commands/_helpers.py`（Yau / 群内记忆分类共用）
- `call_deepseek(user_message, system_prompt, max_tokens=150) -> str` 签名不变；实现改为 `await chat(messages, label="helpers", max_tokens=..., temperature=0.8, timeout=_state.YAU_TIMEOUT)`，返回池文本。
- 失败由 `httpx` 异常变为 `LLMPoolExhausted`（RuntimeError 子类）；调用方 `yau.py`（`except Exception`）与 `character.py`（`except Exception`）均已核实为宽捕获，行为不变（Yau 仍回"AI 暂时罢工了…"）。
- 移除已无用的 `import httpx`。

### 1.3 `scripts/memory_manager.py`（CLI 记忆管理）
- 删除云端常量（`DEEPSEEK_API_KEY/BASE_URL/MODEL`）；`classify_text` 改走 `await chat(..., label="memory_classify", max_tokens=100, timeout=30.0)`。
- **JSON 解析、markdown 清理、`print("  ⚠ LLM 调用失败")` 兜底、返回 `("","")` 全部原样保留**。
- 移除 `import httpx`。双路 import 两条路径均实测：包式（`scripts.memory_manager`）与脚本式（`sys.path[0]=scripts`）。

### 1.4 `scripts/commands/zhouli.py`（周礼）
- 删除 `ZHOULI_PRIMARY_*/ZHOULI_FALLBACK_*`（含 USTC 与 DeepSeek 两个云端端点）与 `import os/httpx`。
- `call_zhouli_api(text, preferred="") -> str` 改走池（`label="zhouli"`, `max_tokens=300`, `temperature=0.9`, `timeout=30`），并记录命中模型。
- `call_zhouli(text) -> str` 对外行为不变：成功返回 `clean_zhouli_text(raw)`，池耗尽/异常返回 `""`（`cmd_zhouli` 仍回"礼官远行未归"）。

### 1.5 `scripts/commands/divination.py`（算卦）
- 原独立 `DIVINATION_API_KEY` 直连块删除，改 `await chat(..., label="divination", max_tokens=_state.DIVINATION_MAX_TOKENS, temperature=0.7, timeout=15.0)`。
- 空内容兜底文案"卦象已出，但解读为空，请换个描述重试。"与 `_state.bot_logger` 日志保留；失败仍向上抛（外层 `cmd_divination` 的"解读服务暂时不可用"回退路径不变，已核实）。
- 移除 `import httpx`；`_state.DIVINATION_*` 仅剩 `max_tokens` 用途。旧 key 仍留在 `.env` 未删（按要求）。

### 1.6 `scripts/user_profile.py`（用户画像）
- 双路兼容 import；`_API_KEY/_API_BASE_URL/_API_MODEL` 旧名保留（`_API_BASE_URL=""`、`_API_MODEL=""` 表"由池决定"），`set_fallback_api_key`（reverse_bot L151 调用）保留为空转兼容，签名不变。
- `_call_single_api(api_key, api_base_url, api_model, prompt, *, preferred=None)` 保留原 4 参签名，改为池调用（`label="profile"`，`max_tokens=512`，`timeout=40`），失败返回 `None`（与原来一致）。
- 新增 `_alternate_pool_model()`：取池内与 active_model 不同的第一个模型作"备用"。
- `_call_profile_api(msgs, existing, school_only)` 三段式结构、校验 `_validate_profile`、返回值、`school_only` 分支全部保留；第二步由"DeepSeek 官方 deepseek-chat"改为"池内备用模型"，日志文案同步更新（"主模型/备用模型"→"首选模型/池内备用模型"）。
- `update_profiles / retry_pending_updates / load_profiles / save_profiles` 未改逻辑。
- **附带健壮性修复（必须说明）**：`logs/` 目录 ACL 异常，`logging.FileHandler(logs/bot.<date>.log)` 在受限环境抛 `PermissionError`，会**在 import 阶段直接让 bot 起不来、也让 PM 规定的自检命令无法执行**（详见第 4 节，决策引擎同款代码仍在）。已把该 FileHandler 包进 `try/except OSError`，失败时降级为仅控制台日志并打印一行警告；`logs/` 可写时行为与原来逐字一致。

### 1.7 `scripts/commands/system.py`（模型测试命令）
- `test_model(name, api_key="", base_url="", model="") -> str` 保留原签名与 `=== … ===` 中文报告风格；改为打一次真实池调用，打印 `Status/Time`、**命中模型**、`Reply`、`Result: ✅/❌`；失败区分 `EXHAUSTED`（池内全挂，附失败明细）与其它异常。
- 新增 `pool_report()`：出口地址、API Key 是否配置、T1–T5 链、单次尝试/总预算/冷却参数、**当前 active_model**、冷却中模型、审计日志落点。
- `cmd_test` 改为输出"模型池配置 + 一次真实调用"；`cmd_switch_model` 仍走 `de.set_active_model` 再用 `preferred=该模型` 实测（`api_key/api_base_url` 用 `.get()` 容错）；`cmd_view_model` 示例模型名更新为 `glm-5.3-flash`。

### 1.8 `scripts/reply_generator.py` —— 未改（死代码）
全仓库 `.py` 引用 **0 处**（grep 仅命中 `KUN.md`、`PROJECT_INDEX.md` 两份文档），无任何模块 import 它，运行时不可达。按"已死代码则只在报告里说明、不要动"的要求保持原样（仍含 `api.deepseek.com` 与 `deepseek-chat` 字样，属死文件）。

---

## 2. 自检与冒烟证据

### 2.1 云端残留（被改 7 文件）
```
PASS: 7 个文件 0 处云端端点/云端模型名残留
（模式：api.llm.ustc | api.deepseek | deepseek-chat | DECISION_API_KEY | DIVINATION_API_KEY"）
```

### 2.2 语法 / import 链
```
SYNTAX_OK  scripts/user_profile.py 14273 chars
SYNTAX_OK  scripts/memory_manager.py 20785 chars
SYNTAX_OK  scripts/commands/zhouli.py 5457 chars
SYNTAX_OK  scripts/commands/divination.py 11118 chars
SYNTAX_OK  scripts/commands/_helpers.py 899 chars
SYNTAX_OK  scripts/commands/_state.py 4071 chars
SYNTAX_OK  scripts/commands/system.py 5985 chars
ALL_SYNTAX_OK

IMPORT_CHAIN_OK: import reverse_bot 通过（已排除已知 logs/ ACL 阻断）
```
- `python -m py_compile` 因 `scripts\__pycache__` 不可写而报 Errno 13（W1 已记录的沙箱限制），故改用内存 `compile()` 做语法检查，并以 `import reverse_bot` 实际导入为更强证据。

### 2.3 真实冒烟（9/9 PASS，全程打本地网关）
`qq_native_re\findings\model_pool\_w2_smoke.py`（可复跑）→ 输出存 `W2_smoke_output.txt`：
```
[PASS] 1 _state 常量: BASE=http://127.0.0.1:8090 MODEL=deepseek-flash KEY=已配置
[PASS] 2 _helpers.call_deepseek (Yau): '好的'
[PASS] 3 character 记忆分类: category=identity text=养了一只叫豆豆的猫
[PASS] 4 memory_manager 分类: category=preferences text=不喜欢吃香菜
[PASS] 5 周礼 call_zhouli: len=214 head=我听闻，从前有位臣子在朝中当差…
[PASS] 6 算卦 call_divination_api: len=63 head=卦得损之遯，三人行减一人…
[PASS] 7 用户画像 _call_profile_api: len=125 alt=deepseek-flash-2
[PASS] 8 system 模型测试: Status: 200 | Time: 1.02s | 命中模型: deepseek-flash | ✅ 连通成功
[PASS] 9 首试假模型自动降档: 首试 no-such-model-xyz → 命中 deepseek-flash ✅
结果：PASS 9 / FAIL 0    exit=0
```

### 2.4 审计留痕（证明真的走了池）
`qq_native_re\findings\model_pool\llm_pool.jsonl` 中本次接线各 label 的最终成功行：
`helpers`(07:58:04) / `memory_classify`(07:58:22, 07:58:24) / `zhouli`(07:58:53) / `divination`(07:59:13) / `profile`(08:00:31) / `system_test`(08:02:03, 08:02:04) — 全部 `ok:true, final:true`，tier 为 `preferred`（active_model=deepseek-flash 已在池内）。

---

## 3. 未动其它文件声明

- **未改**：`reverse_bot.py`、`scripts/decision_engine.py`、`scripts/model_primary.py`、`scripts/model_fallback.py`、`scripts/command_handler.py`、`scripts/commands/*` 中除上述 5 个模块以外的全部文件、`config/*.json`（含 `model_pool.json` 与 `active_model.json`）、`.env`。
- **未动** `config/model_pool.json`：优先级链逐字仍为
  `T1[deepseek-flash, deepseek-flash-2] → T2[deepseek-v4-flash-ascend, deepseek-v4-flash-ascend1] → T3[qwen3.8-flash-next-2, qwen3.8-flash-next] → T4[glm-5.3-flash] → T5[qwen3.8-reasoner]`（该文件 07:58 被其它波次追加了 `min_max_tokens/model_overrides/no_response_format` 三个键，非我所为）。
- **未动** `config/active_model.json`：我备份了原件（当时为 `deepseek-v4-pro`），但**没有改它**；08:00 观测到它已被其它波次改为 `deepseek-flash`（非我所为，符合上级要求）。
- **未触碰** NapCat 目录/配置/进程、官方 QQNT、个人 QQ；未执行任何 `taskkill`；未重启 bot（重启留给 W3）。
- 未改 `scripts/test_*.py` 探针与 `scripts/nekosia_image.py`（后者是图片 API，与模型池无关）。
- 旧常量消费者核查：在线代码中已无任何模块直接用 `_state.DEEPSEEK_*`/`DIVINATION_*` 发请求（仅 `divination.py` 取 `DIVINATION_MAX_TOKENS` 作 token 上限）；`command_handler.py` 从 `_state` 导入的 8 个符号全部存在。

---

## 4. ⚠ 需 PM 决策的两处环境/邻接事项

### 4.1 自检命令在受限沙箱内会被 `logs/` ACL 阻断（**预先存在，非本次引入**）
`cd E:\QQbot; python -c "import reverse_bot; print('OK')"` 在本沙箱内失败：
```
PermissionError: [Errno 13] Permission denied: 'E:\\QQbot\\logs\\bot.2026-10-12.log'
  File "E:\QQbot\reverse_bot.py", line 40, in <module>
  File "E:\QQbot\scripts\decision_engine.py", line 105, in <module>
```
- 根因：`logs/` 目录 ACL 异常（W1 报告已记录，用户决定暂不修）；`logging.FileHandler` 在 **import 阶段**打开 `logs/bot.<date>.log`。
- 证据（非本次引入）：`scripts/decision_engine.py` L105 与该语句在**改动前备份中逐字相同**（`qq_native_re\patches\model_migration_backup_20261012-075541\decision_engine.py`）。
- 我已在我负责的 `user_profile.py` 中加了 `try/except OSError` 降级；**`decision_engine.py` 属其它波次文件，我未越界修改**，建议由其负责人加同样 2 行守卫，否则：
  1) 任何受限 shell 都无法执行 PM 规定的 `import reverse_bot` 自检；
  2) 若 bot 以受限身份重启，会在 import 阶段直接起不来。
- 本次验证用打桩脚本 `qq_native_re\findings\model_pool\_w2_import_check.py`（把 `logging.FileHandler` 换成内存 handler）证明"排除该已知 ACL 后 import 链完整、AGENTS.md 规定的再导出符号存在"。**真实 bot 进程非受限，可正常写 `logs/`，线上不受影响。**

### 4.2 邻接残留（不在我的清单内，供 PM 收口）
1. `reverse_bot.py` L140-148 仍是云端配置：`DECISION_API_BASE = "https://api.llm.ustc.edu.cn/v1"`、`api_model="deepseek-v4-pro"` → **全仓库仅剩的"活"云端端点**（`decision_engine.py` 已由其负责人接线完毕）。
2. `scripts/test_fallback_api.py`、`scripts/test_subprocess_call.py` 两个探针脚本仍含云端端点（探针，非运行链路）。
3. `scripts/reply_generator.py` 死代码仍含云端端点（未改，理由见 1.8）。
4. `config/model_pool.json` 的 `model_overrides."glm-5.3-flash".min_max_tokens: 128` 目前**无实际效果**：`model_min_max_tokens()` 取 `max(全局512, 覆盖值)`，512 胜出（设计如此，仅供 PM 知悉）。
5. `AGENTS.md` 称 reverse_bot 再导出 `processed_msg_ids/PROCESSED_MSG_MAX/known_groups/find_valid_number`，实际 `reverse_bot.py` 只导入 7 个符号（改动前后一致，文档与代码的既有差异，非本次引入）。

---

## 5. 产物清单

| 文件 | 说明 |
|---|---|
| `qq_native_re\patches\model_migration_backup_20261012-075552\` | 9 个待改文件原件备份 |
| `qq_native_re\findings\model_pool\W2_rewire_aux.md` | 本报告 |
| `qq_native_re\findings\model_pool\_w2_smoke.py` | 7 个接线点真弹冒烟（可复跑，exit 0） |
| `qq_native_re\findings\model_pool\W2_smoke_output.txt` | 冒烟原始输出（PASS 9 / FAIL 0） |
| `qq_native_re\findings\model_pool\_w2_import_check.py` | import 链自检（打桩排除 logs/ ACL） |
| `qq_native_re\findings\model_pool\llm_pool.jsonl` | 审计日志（含本次各 label 命中行） |
