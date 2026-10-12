# W2 接线报告：主聊天链路接入 scripts/llm_pool.py

- 时间：2026-10-12 08:05（Asia/Shanghai）
- 执行者：W2 实现子 agent（父 agent：session-d76d464a-44c7-41ec-a536-58729abde67f）
- **复验提示**：本次接线在 `scripts/llm_pool.py` 被兄弟任务加固为 1097 行版（07:59:43）、`config/model_pool.json` 加固（07:58:25，新增 per-model 附加项，**优先级链 5 档逐项未变**）之后，已**再跑一遍全部自检与实弹**，结果一致（见 6.1b、6.2b）。
- 备份目录：`E:\QQbot\qq_native_re\patches\model_migration_backup_20261012-075541\`
  （decision_engine.py 53396B / model_primary.py 4303B / model_fallback.py 4279B / active_model.json 34B / reverse_bot.py 19824B 五个原件）
- 落盘证据：`qq_native_re\findings\model_pool\` 下 `w2_selfcheck.py`、`w2_selfcheck_output.txt`、
  `w2_livefire_primary.txt`、`w2_livefire_fallback.txt`、`w2_import_check.txt`、`llm_pool.jsonl`（审计）
- 红线遵守：未触碰 NapCat / QQ 侧；未重启 bot；未修改 `config/model_pool.json` 的优先级链；未改动任何未授权文件。

## 1. 改动清单

| # | 文件 | 关键行 | 改动 |
|---|------|--------|------|
| 1 | scripts/decision_engine.py | L35-38 双路导入；L41-44 `_pool_available_models()`；L372 `AVAILABLE_MODELS = _pool_available_models()`；L375 `DEFAULT_MODEL="deepseek-flash"`；L386-411 `get_active_model`；L414-417 `set_active_model` 校验；L858 新增 `preferred_model` 字段；L925-931 `_generate_reply_with_failover` 去掉云端 key；L1300-1302 `__main__` | ✅ |
| 2 | scripts/model_primary.py | L31-33 双路导入；L59-93 `generate_reply` 改走 `llm_pool.chat`；L38-42 `_apply_pool_overrides`；L110-127 stdin 解析 + preferred | ✅ |
| 3 | scripts/model_fallback.py | 同上结构（label=model_fallback） | ✅ |
| 4 | config/active_model.json | `{"model": "deepseek-flash"}`（2 空格缩进） | ✅ |
| 5 | reverse_bot.py | **未改动**（依据见第 5 节） | — |

`git diff --stat`：4 files changed, 178 insertions(+), 100 deletions(-)（config/active_model.json 4、decision_engine.py 62、model_fallback.py 108、model_primary.py 104）。

## 2. scripts/decision_engine.py 细节

1. **双路兼容导入（L35-38）**：严格按要求写法，包式导入命中 `scripts.llm_pool`，脚本方式运行命中 `llm_pool`。
2. **`AVAILABLE_MODELS`（L372）**：由 `llm_pool.available_models()` 派生，导入时初始化；实测值（保序去重）：
   `['deepseek-flash', 'deepseek-flash-2', 'deepseek-v4-flash-ascend', 'deepseek-v4-flash-ascend1', 'qwen3.8-flash-next-2', 'qwen3.8-flash-next', 'glm-5.3-flash', 'qwen3.8-reasoner']`
   （8 个模型，与 `config/model_pool.json` 的 5 档链逐项一致，链本身未动。）
3. **`get_active_model()`（L386-411）**：返回 `{"model": active_model.json 的值（非法/缺失→deepseek-flash）, "api_base_url": os.getenv("LLM_POOL_BASE_URL", "http://127.0.0.1:8090/v1"), "api_key": os.getenv("LLM_POOL_API_KEY", "")}`。实测：`{'model': 'deepseek-flash', 'api_base_url': 'http://127.0.0.1:8090/v1', 'api_key': 'sk-personal-****'}`。
4. **`set_active_model()`（L414-417）**：校验改为对 `AVAILABLE_MODELS`（=池链）校验。实测 `set_active_model('deepseek-v4-pro') -> (False, "模型 'deepseek-v4-pro' 不可用")`。
5. **`_call_model_script()`**：子进程机制、stdin/stdout 协议、60s 超时、异常处理全部不变；仅新增 `"preferred_model": active_model["model"]`（L858），已有的 model/api_base_url/api_key 三字段保留（现在都指向本地网关池）。
6. **`_generate_reply_with_failover()`（L906-931）**：「主脚本 → 备脚本」两层熔断保留；删除了 `fallback_key = os.getenv("DEEPSEEK_API_KEY")`，备用脚本改为不带 key 覆盖（= 用池配置 `LLM_POOL_API_KEY`）。**这是必须改的**：若继续把云端 key 传给子进程，子进程会拿它去打本地网关（401/无效 key）。
7. **`__main__`（L1300-1302）**：`DecisionEngine(api_key=os.environ.get("DEEPSEEK_API_KEY",""))` → `DecisionEngine()`（该参数实测从未被消费，见第 5 节）。

## 3. scripts/model_primary.py / model_fallback.py

- 内部单模型 httpx POST 全部替换为 `await chat(messages, label="model_primary"/"model_fallback", max_tokens=..., temperature=0.85, preferred=preferred or None, timeout=30.0)`。
- 保留：stdin 字段 `context/persona/max_tokens/model/api_base_url/api_key`、stdout `{"reply": "..."}`、失败返回空串、空输入/坏 JSON → `{"reply": ""}`、`_NICKNAME_PREFIX` 昵称前缀清理、中文注释风格、`traceback` 兜底。
- 新增：`preferred_model`（优先于旧字段 `model`）；`api_base_url`/`api_key` 非空时映射为 `LLM_POOL_BASE_URL`/`LLM_POOL_API_KEY` 后再调池（子进程单次运行，首次调用前设置，仅影响本进程）。
- 失败时 stderr 一行可诊断信息：`[model_primary] 模型池耗尽: <LLMPoolExhausted 原文，含每个尝试过的模型与原因>`；成功时也打一行 `命中模型=<model>`（供验收取证）。

## 4. config/active_model.json

```json
{
  "model": "deepseek-flash"
}
```

## 5. reverse_bot.py 核查结论：**不改**（有证据）

任务书要求先确认 `DECISION_API_BASE / DECISION_API_KEY / set_fallback_api_key(DEEPSEEK_API_KEY)` 是否真被引擎消费。AST 级证据（对 `DecisionEngine.__init__` 函数体扫描 `self.<参数>` 赋值/引用）：

```
参数 config_path        -> self.config_path 被赋值/引用: False
参数 api_key            -> self.api_key 被赋值/引用: False
参数 api_base_url       -> self.api_base_url 被赋值/引用: False
参数 api_model          -> self.api_model 被赋值/引用: False
参数 fallback_api_key   -> self.fallback_api_key 被赋值/引用: False
```

reverse_bot.py L145-148 传的 4 个关键字（api_key/api_base_url/api_model/fallback_api_key）**只是形参，函数体内一次都没读**；引擎取模型配置只走 `get_active_model()`。故：
- L141 `DECISION_API_BASE` / L140 `DECISION_API_KEY` / L147 `api_model="deepseek-v4-pro"` 是**惰性值**，按任务书"只是存而不用，不要改"处理 → **reverse_bot.py 保持原样**（`git diff` 中它只有 v2.3.2 重构删虚拟股的旧改动，无本次改动）。
- L151 `set_fallback_api_key(DEEPSEEK_API_KEY)` **确实被消费**，但消费方是 `scripts/user_profile.py`（画像模块，不属于本任务范围，已由兄弟 agent 接手），因此也留待该文件接线时一并处理。
- 遗留可选项（交给 W3 决定）：reverse_bot.py L141 那一行露头 `api.llm.ustc.edu.cn`。它不参与任何调用（见上），若要"全仓字面清零"可把 L140-141、L145-148 一并删/改；本任务按指令未动。

## 6. 自检与实弹原文

### 6.0 环境限制（重要，供 W3 验收参考）

本会话的 DSH 沙箱**只允许受限进程写 `E:\QQbot\qq_native_re\**`**，写 `logs\`、`config\`、`scripts\` 一律 `PermissionError`；且 `asyncio.create_subprocess_exec` 在本沙箱下**必然 WinError 5**（连 `python -c "print(42)"` 这种无关命令都失败），而普通 `subprocess.Popen` 正常。实测探针（原始输出见 `w2_selfcheck_output.txt`）：

```
[0] asyncio.create_subprocess_exec 探针 -> 沙箱拒绝 PermissionError: [WinError 5] 拒绝访问。
[0] 写探针 logs/_w2_probe.tmp -> 沙箱拒绝 PermissionError: [Errno 13] Permission denied
[0] 写探针 config/_w2_probe.tmp -> 沙箱拒绝 PermissionError: [Errno 13] Permission denied
[0] 写探针 qq_native_re/findings/model_pool/_w2_probe.tmp -> OK
```

因此任务书里的 `python -c "import reverse_bot; print('OK')"` **在本沙箱内任何 agent 都跑不通**：`scripts/decision_engine.py` L105 在模块级就为 `logs\bot.<date>.log` 建 `logging.FileHandler`（这段代码本次**未改动**，git diff 里不含它），受限进程无 logs\ 写权限 → PermissionError。

可用的等价自检命令（只把文件日志处理器换成 NullHandler，其余全真）：

```powershell
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" -c "import logging; logging.FileHandler=lambda *a,**k: logging.NullHandler(); import reverse_bot; print('OK (logging.FileHandler 打桩版)')"
# 输出：OK (logging.FileHandler 打桩版)
```

真实 bot 是用户态（非沙箱）进程，logs\、config\ 都有 `Authenticated Users: Modify`，不受此限制；`logs\bot.2026-10-12.log` 今天 07:32 仍在正常增长即证。

### 6.1 综合自检 `w2_selfcheck.py` 原文（节选，完整见 `w2_selfcheck_output.txt`）

该脚本在**测试进程内**替换两处（`logging.FileHandler`→NullHandler、`asyncio.create_subprocess_exec`→Popen 版 shim，两者都不是本次改动内容），其余全真：

```
[1] import reverse_bot: OK
[2] AVAILABLE_MODELS = ['deepseek-flash', 'deepseek-flash-2', 'deepseek-v4-flash-ascend', 'deepseek-v4-flash-ascend1', 'qwen3.8-flash-next-2', 'qwen3.8-flash-next', 'glm-5.3-flash', 'qwen3.8-reasoner']
[2] get_active_model = {'model': 'deepseek-flash', 'api_base_url': 'http://127.0.0.1:8090/v1', 'api_key': 'sk-personal-****'}
[2] set_active_model('deepseek-v4-pro') -> (False, "模型 'deepseek-v4-pro' 不可用")
[2] set_active_model('deepseek-flash') -> (False, "写入配置失败: [Errno 13] Permission denied: 'E:\\QQbot\\config\\active_model.json'")   ← 仅沙箱写限制所致；真实 bot 有 Modify 权限
[3] _call_model_script(model_primary)  -> '这种天气坐在窗边喝红茶最舒服了呢…大家有想好去哪里吗？'
[3] _call_model_script(model_fallback) -> '天气确实很好呢…这种日子待在室内有点可惜。大家有打算去哪里吗？'
[3] _generate_reply_with_failover -> '天气是很好呢…不过我今天有吹奏乐部的练习，只能在室内度过了。出去玩的话，记得涂防晒哦。'
[3b] 主脚本失效时 _generate_reply_with_failover -> '天气确实很好呢。这种日子在阳台喝杯红茶，晒晒太阳就很舒服了哦。'   ← 主失败→备脚本这层熔断仍有效
[3] 三次调用全部拿到非空回复: True
[4] stdin JSON: {"context": "[小明]: 今天天气真好啊\n[小红]: 是啊适合出去玩", "persona": "## 核心身份\n你是长崎素世…", "max_tokens": 50, "model": "deepseek-flash", "preferred_model": "deepseek-flash", "api_base_url": "http://127.0.0.1:8090/v1", "api_key": "sk-personal-****"}
SELFCHECK DONE
```

### 6.1b 复跑（08:05，针对加固后的 llm_pool.py 1097 行版 + model_pool.json 07:58:25 版）

```
[0] asyncio.create_subprocess_exec 探针 -> 沙箱拒绝 PermissionError: [WinError 5] 拒绝访问。
[0] 写探针 qq_native_re/findings/model_pool/_w2_probe.tmp -> OK
[1] import reverse_bot: OK
[2] AVAILABLE_MODELS = ['deepseek-flash', 'deepseek-flash-2', 'deepseek-v4-flash-ascend', 'deepseek-v4-flash-ascend1', 'qwen3.8-flash-next-2', 'qwen3.8-flash-next', 'glm-5.3-flash', 'qwen3.8-reasoner']
[2] get_active_model = {'model': 'deepseek-flash', 'api_base_url': 'http://127.0.0.1:8090/v1', 'api_key': 'sk-personal-****'}
[2] set_active_model('deepseek-v4-pro') -> (False, "模型 'deepseek-v4-pro' 不可用")
[3] _call_model_script(model_primary)  -> '这么好的天气，倒是很适合在阳台泡一壶红茶呢。大家有什么想去的地方吗？'
[3] _call_model_script(model_fallback) -> '天气好的时候确实会想出去走走呢。不过要注意防晒，傍晚的紫外线比想象的强哦。'
[3] _generate_reply_with_failover -> '这种天气坐在窗边喝红茶最舒服了呢…不过大家要出去玩的话，记得做好防晒哦。'
[3b] 主脚本失效时 _generate_reply_with_failover -> '天气好的时候很适合在阳台上喝红茶呢…不过今天还得练贝斯，只能隔着窗户看看了。'
[3] 三次调用全部拿到非空回复: True
SELFCHECK DONE
```

（`llm_pool.py` 的 4 个导入符号 `chat / chat_sync / available_models / LLMPoolExhausted` 在加固版中依然齐备。）

### 6.2 两次子进程实弹原文（任务书指定命令）

```
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
'{"context":"甲：今天天气不错\n乙：是啊","persona":"你是群聊机器人，简短回复","max_tokens":50}' | & ".\venv\Scripts\python" scripts\model_primary.py
{"reply": "适合出去走走"}
[model_primary] 调用成功: 命中模型=deepseek-flash

'{"context":"甲：今天天气不错\n乙：是啊","persona":"你是群聊机器人，简短回复","max_tokens":50}' | & ".\venv\Scripts\python" scripts\model_fallback.py
{"reply": "适合出去走走"}
[model_fallback] 调用成功: 命中模型=deepseek-flash
```

补充实弹：带 `preferred_model` + `api_base_url` 的调用 → `{"reply": "早上六点半吧"}`（命中 deepseek-flash）；空 stdin → `{"reply": ""}`（协议健壮性不变）。

### 6.2b 复跑实弹（08:05，加固版池）

```
{"reply": "适合出去玩"}     + [model_primary] 调用成功: 命中模型=deepseek-flash
{"reply": "适合出去走走"}   + [model_fallback] 调用成功: 命中模型=deepseek-flash
```

审计落盘（`qq_native_re\findings\model_pool\llm_pool.jsonl`，`logs\` 因沙箱不可写由 llm_pool 自愈到此）：

```
{"ts": "2026-10-12T08:01:44+08:00", "label": "model_primary",  "tier": "preferred", "model": "deepseek-flash", "ok": true, "latency_ms": 648,  "err": null, "tokens": 1602, "final": true}
{"ts": "2026-10-12T08:01:46+08:00", "label": "model_fallback", "tier": "preferred", "model": "deepseek-flash", "ok": true, "latency_ms": 1485, "err": null, "tokens": 1602, "final": true}
{"ts": "2026-10-12T08:01:48+08:00", "label": "model_primary",  "tier": "preferred", "model": "deepseek-flash", "ok": true, "latency_ms": 744,  "err": null, "tokens": 1611, "final": true}
{"ts": "2026-10-12T08:01:50+08:00", "label": "model_fallback", "tier": "preferred", "model": "deepseek-flash", "ok": true, "latency_ms": 1398, "err": null, "tokens": 1604, "final": true}
```

## 7. grep 零残留（被改文件范围）

```
PS> Select-String -Path scripts\decision_engine.py,scripts\model_primary.py,scripts\model_fallback.py,config\active_model.json -Pattern "api\.llm\.ustc|api\.deepseek|deepseek-chat"
0 命中 —— 干净
```

「被改文件」四件全部零残留。全仓仍剩 **5 个文件共 8 处**命中，均在**本任务授权范围之外**，供 W3 收口参考：

| 文件 | 处数 | 说明 |
|------|------|------|
| reverse_bot.py L141 | 1 | `DECISION_API_BASE`，惰性值，未参与调用（第 5 节已证） |
| config/decision_rules.json L40 | 1 | `failover.models.fallback.api_base_url`；引擎只读 `failover.content_checks`（L201-202），该块**是死配置**，不产生任何调用 |
| reply_generator.py | 2 | 全仓无任何 import 引用（孤立文件，未接入 bot） |
| scripts/test_fallback_api.py、scripts/test_subprocess_call.py | 4 | 手工调试脚本，不在运行链路 |

（`scripts/commands/_state.py`、`zhouli.py`、`_helpers.py`、`divination.py`、`memory_manager.py`、`user_profile.py`、`system.py` 早前也有残留，现已由兄弟 agent 改完，本报告采集时已无命中。）

## 8. 未动文件声明

除第 1 节列出的 4 个文件外，**未修改任何其它文件**（含 `scripts/llm_pool.py`、`config/model_pool.json`、`.env`、`reverse_bot.py` 及全部 NapCat/QQ 相关路径）。新增文件仅在 `qq_native_re\findings\model_pool\`（报告与证据）与 `qq_native_re\patches\model_migration_backup_20261012-075541\`（备份）。

## 9. 收尾结论（≤300 字）

已把主聊天链路接到 scripts/llm_pool.py：`decision_engine.py`（AVAILABLE_MODELS 派生自池、get/set_active_model 走本地网关、stdin 新增 preferred_model、failover 去掉云端 key、__main__ 去 key）、`model_primary.py`/`model_fallback.py`（单模型 POST → `llm_pool.chat`，协议与昵称清理不变）、`active_model.json` → deepseek-flash；4 文件零云端残留（grep 0 命中）。自检：打桩版 `import reverse_bot` OK（裸命令因沙箱无 logs\ 写权限失败，与改动无关）；两次子进程实弹分别拿到 `{"reply": "适合出去走走"}`，命中模型均为 deepseek-flash；引擎级 `_call_model_script`+`_generate_reply_with_failover` 三次全部非空，主脚本损坏时备用脚本仍回复（两层熔断保留）。**reverse_bot.py 未改**：AST 证明其传入的 api_key/api_base_url/api_model/fallback_api_key 四个形参从未被消费，按任务书"存而不用则不改"处理；L151 `set_fallback_api_key` 由 user_profile.py 消费，属兄弟任务。备份在 `qq_native_re\patches\model_migration_backup_20261012-075541\`。密钥一律脱敏 sk-personal-****。未触碰 NapCat/QQ，未重启 bot。
