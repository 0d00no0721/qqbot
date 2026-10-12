# W3 收口：FileHandler OSError 守卫 + 惰性云端字面量清理

状态：**已完成**（改后文件 + 自检输出 + 报告均已落盘）
备份目录：`qq_native_re\patches\model_migration_backup_20261012-080737_closeout\`
（含 decision_engine.py / reverse_bot.py / decision_rules.json 改前原件，可逐字回滚）

## 0. 基线复现（改动前，裸命令）

```
& ".\venv\Scripts\python" -c "import reverse_bot; print('OK')"
→ PermissionError: [Errno 13] Permission denied: 'E:\\QQbot\\logs\\bot.2026-10-12.log'
  File "E:\QQbot\reverse_bot.py", line 40, in <module>  →  from scripts.decision_engine import DecisionEngine
  File "E:\QQbot\scripts\decision_engine.py", line 105, in <module>
EXIT=1
```

**关键发现**：崩点是 decision_engine.py L105，但 `reverse_bot.py` L127 有**同款未守卫**的模块级
FileHandler。只修前者的话 import 会在紧接着的 L127 再次失败——故两者必须同修，裸命令才可能通过。

## 1. scripts/decision_engine.py — 改动前后对照

改动前 L102-110：

```python
102: # 文件日志（按启动日期命名，避免 TimedRotatingFileHandler 在 Windows 下的 os.rename 竞态）
103: from datetime import datetime as _dt
104: _LOG_DATE = _dt.now().strftime("%Y-%m-%d")
105: _file_handler = logging.FileHandler(
106:     os.path.join(LOG_DIR, f"bot.{_LOG_DATE}.log"),
107:     encoding="utf-8",
108: )
109: _file_handler.setFormatter(logging.Formatter("[%(asctime)s] [熔断] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
110: logger.addHandler(_file_handler)
```

改动后 L102-116：

```python
102: # 文件日志（按启动日期命名，避免 TimedRotatingFileHandler 在 Windows 下的 os.rename 竞态）
103: # 注：受限环境下 logs/ 可能整体不可写（本项目已知 ACL 问题），此时退化为"仅控制台日志"，
104: #     绝不因为日志文件打不开而阻断 import（否则整个机器人都起不来）。
105: from datetime import datetime as _dt
106: _LOG_DATE = _dt.now().strftime("%Y-%m-%d")
107: try:
108:     _file_handler = logging.FileHandler(
109:         os.path.join(LOG_DIR, f"bot.{_LOG_DATE}.log"),
110:         encoding="utf-8",
111:     )
112:     _file_handler.setFormatter(logging.Formatter("[%(asctime)s] [熔断] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
113:     logger.addHandler(_file_handler)
114: except OSError as _e:
115:     # 例：PermissionError（logs/ 目录被 ACL 拒绝）——降级为仅控制台日志
116:     _console.stream.write(f"[警告] 无法写入文件日志 logs\\bot.{_LOG_DATE}.log（权限不足），已降级为仅控制台日志\n")
```

写法与兄弟任务的 `scripts/user_profile.py` L58-67 完全同构（含同款中文注释与降级警告）。
`LOG_DIR` 创建（L100 `os.makedirs`，原本已是 `exist_ok=True`）行为不变。

## 2. reverse_bot.py — 两处改动

**(a) L127-135 同款 FileHandler 守卫**（必要项，见 §0）：`_file_handler = None` 预置后包
try/except OSError，失败时降级为仅控制台并打印同款中文警告。语义与备份件逐字一致，仅新增兜底分支。

**(b) L144-153 三处惰性云端字面量改指本地网关**：

| 位置 | 改前 | 改后 |
|---|---|---|
| L146 `DECISION_API_BASE` | `https://api.llm.ustc.edu.cn/v1` | `http://127.0.0.1:8090/v1` |
| L152 `api_model=` | `"deepseek-v4-pro"` | `"deepseek-flash"` |
| L145 `DECISION_API_KEY` | `os.getenv("DECISION_API_KEY", "")` | **未改**（读取方式原样保留，仅值来源不变） |

保守改法：仅改常量值，**未删除任何代码结构、未改 `DecisionEngine(...)` 调用形状、未改形参名/顺序**。
另加 3 行注释说明该三值在 `DecisionEngine.__init__` 内从未被消费（AST 已证），实弹出口由 `scripts/llm_pool.py` 决定。

## 3. config/decision_rules.json

L40 `failover.fallback.api_base_url`：`https://api.deepseek.com/v1` → `http://127.0.0.1:8090/v1`。
**仅改该字符串值**，JSON 结构与 2 空格缩进逐字不变；改后 `json.load` 通过（见 §5）。
该块为死配置（引擎只读 `failover.content_checks`），结构未动。

## 4. 全仓模块级 FileHandler 清单（reverse_bot.py + scripts/**）

| # | 位置 | 处置 |
|---|---|---|
| 1 | `scripts/decision_engine.py:105`（现 L107-116） | **本次加 try/except OSError** ✅ |
| 2 | `reverse_bot.py:127`（现 L127-135） | **本次加 try/except OSError**（不改则裸命令仍失败）✅ |
| 3 | `scripts/user_profile.py:59` | 兄弟任务已守卫（L58 `try` / L65 `except OSError`），本次未动 ✅ |

全仓另无 `RotatingFileHandler` / `TimedRotatingFileHandler` / `logging.basicConfig`。
（`qq_native_re\findings\model_pool\` 下 `_w2_import_check.py` / `w2_selfcheck.py` 里的
`logging.FileHandler` 是历史打桩验证脚本，非生产代码路径，未动。）

## 5. 验证（全部真跑，原文粘贴）

**[1] 裸命令自检（不打桩）— 通过**

```
> cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
> & ".\venv\Scripts\python" -c "import reverse_bot; print('OK')"

[警告] 无法写入文件日志 logs\bot.2026-10-12.log（权限不足），已降级为仅控制台日志
[画像] 警告: 文件日志不可用([Errno 13] Permission denied: 'E:\\QQbot\\logs\\bot.2026-10-12.log')，已降级为控制台日志
[警告] 无法写入文件日志 logs\bot.2026-10-12.log（权限不足），已降级为仅控制台日志
OK
EXIT=0
```

三行警告分别来自 decision_engine（本次）、user_profile（兄弟任务）、reverse_bot（本次）——
即三个 FileHandler 全部走降级分支且**均未阻断 import**。改动前同一命令 EXIT=1。

**[2] logs/ 可写路径回归 — 行为与改动前逐字一致**

把改后的 `decision_engine.py` 复制到可写目录下当真实模块执行（非打桩，真实走 `try` 正分支）：

```
LOG_DIR = ...\_w3_probe\logs
logs/ 被创建 = True
FileHandler 数量 = 1                ← 可写时照旧挂载，未退化为仅控制台
baseFilename 文件名 = bot.2026-10-12.log
formatter._fmt     = [%(asctime)s] [熔断] %(levelname)s: %(message)s
formatter.datefmt  = %Y-%m-%d %H:%M:%S
encoding           = utf-8
--- 落盘内容 ---
[2026-10-12 08:08:47] [熔断] INFO: 可写路径写入测试
EXIT=0
```

文件名为日期格式、formatter 格式串、datefmt、encoding=utf-8 全部与备份件逐字相同，真实落盘成功 →
**logs/ 可写时生产行为零变化**（探针目录已清理）。

**[3] grep 云端残留 — NO_MATCH**

```
> Select-String -Path "reverse_bot.py","config\decision_rules.json" `
    -Pattern "api\.llm\.ustc|api\.deepseek|deepseek-v4-pro"
NO_MATCH（两文件均无云端残留）
```

**[4] JSON 合法性**

```
JSON_OK
failover.fallback = {"_comment": "备用模型（与主模型相同，同一密钥）", "api_key_env": "DEEPSEEK_API_KEY",
                     "api_base_url": "http://127.0.0.1:8090/v1", "model": "deepseek-v4-flash",
                     "api_format": "openai"}
```

**[5] 惰性残留登记（本任务范围外，未动）**

```
reply_generator.py:20:     DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"     ← 全仓 0 引用死代码
test_fallback_api.py:4:    url = "https://api.deepseek.com/v1/chat/completions"  ← 手工调试脚本
test_subprocess_call.py:8: "api_base_url": "https://api.deepseek.com/v1/..."     ← 手工调试脚本
```

## 6. 未动文件声明

本次**只**修改了 3 个文件：`scripts/decision_engine.py`、`reverse_bot.py`、`config/decision_rules.json`。

- **未动 `config/model_pool.json`**（优先级链，T5 由 PM 另派）
- **未动 `scripts/llm_pool.py`** 及任何模型调用点脚本
- 未动 `.env`、未动 `reply_generator.py` / `test_fallback_api.py` / `test_subprocess_call.py`（仅登记）
- 未重启 bot、未碰 NapCat / 官方 QQNT / 个人 QQ
- 改动在下次用户重启 bot 时生效（本次未重启，运行中的 bot 不受影响）

**风险提示**：`reverse_bot.py` 的 FileHandler 守卫属本任务「顺带 grep 一并守卫」授权范围内的必要扩展——
不修则裸命令仍在 L127 失败，任务可验证性目标无法达成。该扩展已在 §0/§4 说明，请 PM 知悉。
