# KUN.md — QQbot 项目快速参考

## 📌 项目定位

基于 **OneBot V11** 协议的 QQ 群聊机器人。NapCat（QQ NT 客户端） + Python WebSocket 服务端。集成多模型 AI 决策引擎实现上下文驱动的智能回复。部署在 **Windows** 环境。

- 机器人 QQ：`2668851638`
- 主群：`755471390`，二号机群：`284517232`
- 管理员：`784427550`
- 当前版本：`2.2.3.2`
- 当前角色：`soyo nagasaki`（长崎素世）
- 当前模型：`deepseek-v4-flash-ascend`（中科大代理）

---

## 🏗 架构总览

```
用户 QQ 群
    │
    ▼
NapCat (QQ NT 客户端, 协议注入+封装)  ←→  QQ NT 服务器
    │  WS 主动连接 :8080
    ▼
reverse_bot.py  ─── 主入口，WS 服务端 (555行)
    │
    ├──▶ scripts/command_handler.py  (2062行)  — 所有 @bot 指令
    │
    ├──▶ scripts/decision_engine.py  (~1000行) — 自主发言决策
    │      ├── subprocess ─▶ model_primary.py  (主模型)
    │      └── subprocess ─▶ model_fallback.py (备用模型)
    │
    ├──▶ scripts/user_profile.py     — 用户画像 (缓冲+定时API)
    │
    └──▶ scripts/zero_shot_classifier.py — 零样本分类器 (sentence-transformers)
```

**端口分配：**
| 端口 | 角色 |
|------|------|
| 8080 | reverse_bot WebSocket 服务端，NapCat 主动连接 |
| 6700 | NapCat WS 服务端（standalone_bot 用） |
| 6099 | NapCat HTTP API 管理接口 |

---

## 📁 文件结构 & 职责速查

### 根目录入口

| 文件 | 行数 | 职责 | 关键内容 |
|------|------|------|----------|
| `reverse_bot.py` | 555 | **主入口** — WS 服务端 | 消息接收/发送、+1 检测、决策引擎调用、后台任务(画像/神秘数字) |
| `standalone_bot.py` | 170 | 备选 WS 客户端模式 | 精简版，仅菜单功能，连接 NapCat:6700 |
| `auto_config.py` | 156 | **已废弃** | NapCat + NoneBot2 自动配置（对当前架构无效） |
| `analyze_model_success.py` | 317 | 日志分析工具 | 分析主模型/备用模型成功率（手动运行） |
| `set_env.py` | 6 | 设置 HF 缓存路径 | 供手动调试用，无代码 import 它 |

### scripts/ — 核心模块

| 文件 | 行数 | 职责 | 关键导出/类 |
|------|------|------|-------------|
| `command_handler.py` | 2062 | **所有 @bot 指令** | `handle_command()` `init_handlers()` `call_deepseek()` `call_zhouli()` `perform_divination()` `tokenize_and_parse()` |
| `decision_engine.py` | ~1000 | **决策引擎** | `DecisionEngine` 类，`ContextRingBuffer` 类 |
| `user_profile.py` | ~600 | **用户画像** | `record_message()` `update_profiles()` `get_profile()` |
| `zero_shot_classifier.py` | ~300 | **零样本分类器** | `ZeroShotClassifier` 类，按需加载 |
| `model_primary.py` | ~120 | 主模型子进程 | stdin/stdout JSON 通信 |
| `model_fallback.py` | ~120 | 备用模型子进程 | stdin/stdout JSON 通信 |
| `group_manager.py` | ~400 | 群功能管理终端 | 交互式 CLI |
| `memory_manager.py` | ~500 | 角色记忆管理终端 | 交互式 CLI |
| `sign_in.py` | ~120 | 签到 | `process_sign_in()` |
| `mystery_number.py` | ~70 | 神秘数字 | `find_valid_number_async()` |
| `nekosia_image.py` | ~40 | 猫娘图片 | `fetch_catgirl_image()` |
| `pixiv_helper.py` | 153 | Pixiv 图片 | `fetch_random_pixiv_image()` `download_pixiv_image()` |
| `reply_generator.py` | ~120 | DeepSeek API 封装 | |
| `download_model.py` | ~35 | HF 模型预下载 | |
| `jm_check_id.py` | ~90 | JMComic 检查 | |

### config/ — 配置体系

| 文件 | 职责 | 关键内容 |
|------|------|----------|
| `active_character.json` | 角色开关&管理员 | `{"enabled": true, "character": "soyo nagasaki", "admin_ids": ["784427550"]}` |
| `active_model.json` | 当前主模型 | `{"model": "deepseek-v4-flash-ascend"}` |
| `decision_rules.json` | 决策引擎参数 | 冷却时间、密度阈值、分类器降级阈值、故障转移配置 |
| `group_features.json` | 群功能开关 | 每个群 5 个独立开关 |
| `group_characters.json` | 群→角色映射 | `{"755471390": "soyo nagasaki"}` |
| `group_defaults.json` | 新群默认值 | 新群功能初始化模板 |
| `persona.txt` | 默认人设 | 173 字默认人设 |
| `labels.json` | 分类标签（旧版） | `["日常生活", "严肃政治", "战争雷霆"]` |
| `characters/soyo nagasaki/` | 角色库 | info.json / persona.txt / memories/ |

---

## 🚀 启动方法

```bash
# 1. 克隆后首次搭建
cd E:\QQbot
python -m venv venv
pip install -r requirements.txt
copy .env.example .env              # 然后编辑 .env 填入 API Key
python scripts/download_model.py    # 预下载分类模型

# 2. 安装 pixivtools（可选，Pixiv功能需要）
cd pixivtools-src/pixivtools-main && pip install . && cd ..\..

# 3. 启动 NapCat QQ 客户端
start "" "E:\QQbot\NapCat\NapCat.44498.Shell\NapCat.44498.Shell\NapCatWinBootMain.exe"

# 4. 启动主机器人（WS 服务端 :8080）
cd E:\QQbot
PYTHONIOENCODING=utf-8 ".\\venv\\Scripts\\python" reverse_bot.py

# 5. 管理工具
PYTHONIOENCODING=utf-8 python scripts/group_manager.py    # 群功能管理
PYTHONIOENCODING=utf-8 python scripts/memory_manager.py    # 角色记忆管理
```

### Git 推送方法

```bash
# remote: https://github.com/0d00no0721/qqbot.git
# 代理: http://127.0.0.1:15715 (Clash/V2Ray) — Git 全局已配置
# 凭据: Git Credential Manager for Windows (已缓存)

git add <文件>                       # 暂存
git commit -m "类型: 描述"            # 提交
git tag <标签名>                     # 创建标签 (不能用空格)
git push origin main --tags          # 推送 commit + 标签到 GitHub
```

---

## ⚡ 消息处理全流程（reverse_bot.py）

```
NapCat 发来 JSON → handler() 回调
    │
    ├─ json.loads(message)
    │
    ├─ post_type == "message" + message_type == "group"  →  _safe_handle_message()
    │   │
    │   ├─ 消息去重 (processed_msg_ids 集合, max 200)
    │   ├─ 语音转文字 (NapCat HTTP API /fetch_ptt_text)
    │   ├─ 清理 CQ 码, 检查 @bot
    │   ├─ 纯图片/表情 → 忽略
    │   │
    │   ├─ @bot + commands 功能启用 →  handle_command()  ← 2062行指令表
    │   │   └─ 匹配到指令 → 处理 → 发送 → return
    │   │
    │   ├─ +1 复读检测 (plus_one 功能启用)
    │   │   └─ 两人连发相同内容 → 机器人也跟一个
    │   │
    │   ├─ 名字/别名检测 (decision_engine 功能启用)
    │   │   └─ 消息含角色别名 → 视为 @bot
    │   │
    │   ├─ 决策引擎 (decision_engine 功能启用)
    │   │   ├─ add_message() 写上下文环形缓冲区
    │   │   ├─ 若 profile_enabled → 注入用户画像
    │   │   └─ should_reply() → 决策是否发言
    │   │
    │   └─ 用户画像缓冲 (profile_record 功能启用)
    │
    └─ post_type == "notice" + notice_type == "group_increase" →  新人入群欢迎
```

---

## 🧠 决策引擎（decision_engine.py）细节

### 消息处理流水线 `should_reply()`

```
1. @bot 强制回复（最高优先级，不走过滤）
2. 前置过滤：
   - 长度 <3 → 跳过
   - 纯 emoji → 跳过
3. 冷却检查：同群距上次回复 <10s → 跳过
4. 密度检查：>15条/分钟 → 跳过
5. 零样本分类 (sentence-transformers)：
   - 置信度 >= 0.45 → 分类决策
   - 置信度 < 0.45 → 降级为纯密度阈值决策
6. 生成回复（子进程 subprocess）：
   - 主模型 model_primary.py (中科大代理)
   - 失败 → 备用模型 model_fallback.py (DeepSeek 官方)
7. 内容安全过滤 (error_keywords 列表)
8. 精确去重 + 相似度去重
```

### ContextRingBuffer

- 每群一个环形缓冲区，maxlen=50
- `get_messages()` 返回最近 N 条（默认 30）
- `get_density()` 计算消息密度（条/分钟）

### 子进程通信协议

```
stdin:  {"messages": [...], "character": {...}, "model": {...}}
stdout: {"reply": "..."} 或 {"error": "..."}
```

---

## 📋 所有 @bot 指令（command_handler.py）

| 类别 | 指令 | 响应 | 代码位置 |
|------|------|------|----------|
| **帮助** | `帮助` / `help` | 类别概览 | `HELP_CATEGORIES` 字典 |
| | `菜单帮助` / `小巧思帮助` / `角色帮助` / `系统帮助` / `日常帮助` | 子类别详细指令 | `HELP_CATEGORIES` 字典 |
| **菜单** | `添加菜单 <菜名>` / `加菜 <菜名>` | 添加到个人菜单 | `add_dish()` |
| | `删除菜单 <菜名>` / `删菜 <菜名>` | 删除菜品 | `remove_dish()` |
| | `查看菜单` / `我的菜单` | 显示列表 | `get_personal_menu()` |
| | `今天吃啥` / `群里吃啥` | 全群随机 | `get_all_dishes()` + random |
| | `今天吃啥 自己` / `我自己吃啥` | 个人随机 | `get_personal_menu()` + random |
| | `选择 <A> <B> ...` | 随机选一个 | `random.choice()` |
| **Yau** | `Yau <话>` | 丘成桐风格对话 | `call_deepseek()` + `yau_system_prompt`（来自 `yau_style.txt`） |
| **周礼** | `周礼 <话>` | "合乎周礼"翻译腔 | `call_zhouli()` → 主模型 / 备用模型 |
| **加密** | `加密 <文字>` | 咕嘎密文 | `encrypt()` 比特级编码 |
| **解密** | `解密 <咕嘎密文>` | 明文 | `decrypt()` |
| **算卦** | `算卦 [事件]` / `占卜 [事件]` | 卦象 + AI解读 | `perform_divination()` → `call_divination_api()` |
| **猫娘** | `猫娘` | 随机猫娘图片 | `fetch_catgirl_image()` (Nekosia API) |
| **Pixiv** | `pixiv随机日榜` | 日榜随机图 | `fetch_random_pixiv_image("daily")` |
| | `pixiv随机周榜` | 周榜随机图 | `fetch_random_pixiv_image("weekly")` |
| | `pixiv随机月榜` | 月榜随机图 | `fetch_random_pixiv_image("monthly")` |
| **像素画** | `像素画`（需同时发图） | 像素预览图+LaTeX | `download_image()` → `pixelate_and_create_tex()` |
| **钢琴** | `钢琴 <曲谱>` | WAV 音频文件 | `tokenize_and_parse()` → `_piano_synthesize()` |
| | `钢琴 帮助` | 编码规范文档 | 发送 SimPiano.md 内容 |
| **角色** | `添加记忆 <内容>` | 分类后写入群记忆 | `_classify_memory()` → save_group_memories() |
| | `删除记忆 <关键词>` | 删除匹配条目 | 管理员/本人可删 |
| | `查看记忆` | 发送记忆文件 | 角色记忆+群记忆合并文件 |
| | `切换角色 <角色名>` | 切换（管理员） | `set_active_character()` |
| | `角色列表` | 列出所有角色 | `list_characters()` |
| | `启用角色` / `停用角色` | 开关角色模式 | `set_active_character()` |
| **签到** | `签到` | 签到成功+排名 | `process_sign_in()` |
| **神秘数字** | `神秘数字` | JMComic ID | `find_valid_number()`（每5s处理一个） |
| **系统** | `test` | API 连通性测试 | `test_model()` 双模型 |
| | `切换模型 <模型名>` / `切模型 <模型名>` | 切换主模型+测试 | `set_active_model()` + `test_model()` |
| | `查看模型` / `模型列表` | 可用模型列表 | `AVAILABLE_MODELS` |

### 频率限制

| 功能 | 限制 | 锁 |
|------|------|----|
| Yau | 每小时 3 次/用户 | `_yau_lock` + `yau_usage` dict |
| 周礼 | 每小时 3 次/用户 | `_zhouli_lock` + `zhouli_usage` dict |
| 神秘数字 | 每 5 秒处理一个 | `_mystery_queue` asyncio.Queue |

---

## 🔑 API 整合

| 用途 | API Key 环境变量 | 端点 | 超时 |
|------|-----------------|------|------|
| 决策引擎主模型 | `DECISION_API_KEY` | `api.llm.ustc.edu.cn/v1` | 子进程无单独超时 |
| 决策引擎备用 | `DEEPSEEK_API_KEY` | `api.deepseek.com/v1` | 同上 |
| Yau | `DEEPSEEK_API_KEY` | `api.deepseek.com/v1` | 15s |
| 周礼主模型 | `DECISION_API_KEY` | `api.llm.ustc.edu.cn/v1` | 30s |
| 周礼备用 | `DEEPSEEK_API_KEY` | `api.deepseek.com/v1` | 30s |
| 算卦解读 | `DIVINATION_API_KEY` | `api.deepseek.com/v1` | 15s |
| 用户画像 | `DEEPSEEK_API_KEY` (fallback) | `api.deepseek.com/v1` | 40s |
| 记忆分类 | `DEEPSEEK_API_KEY` | `api.deepseek.com/v1` | 同 Yau 15s |
| 语音转写 | 无（本地 API） | NapCat HTTP `:6099/fetch_ptt_text` | 10s |

**.env 文件在项目根目录，`reverse_bot.py` 启动时调用 `load_dotenv()` 加载。**

---

## 👥 群功能开关（group_features.json）

每个群 5 个独立功能：

| 开关 key | 用途 |
|----------|------|
| `decision_engine` | 决策引擎自动回复（含别名匹配） |
| `plus_one` | +1 复读检测 |
| `commands` | @bot 指令响应 |
| `newcomer_welcome` | 新人入群欢迎 |
| `profile_record` | 用户画像记录 |

操作方式：`python scripts/group_manager.py` 交互式管理。

---

## 🎭 角色 & 记忆系统

### 数据流向

```
config/active_character.json  ←  全局角色开关 / 管理员
config/group_characters.json  ←  群→角色映射（未配置的群用全局默认）
config/characters/{角色}/     ←  角色库
    ├── info.json             ←  角色名 + 别名
    ├── persona.txt           ←  角色人设
    └── memories/
        ├── character/        ←  角色通用记忆（memory_manager.py 管理）
        └── group/{gid}.json  ←  群专属记忆（@bot 添加记忆 产生）
```

### 别名检测

`decision_engine.get_all_character_aliases(gid)` 返回当前群角色的所有别名。
消息中包含任意别名 → 视为 @bot，绕过冷却/密度检查。

### 记忆分类（LLM 分类）

`添加记忆 <自然语言>` → LLM 分类为 6 类之一：
`identity(身份)` / `relationships(关系)` / `beliefs(信念)` / `knowledge(知识)` / `events(经历)` / `preferences(偏好)`

---

## 🎹 钢琴（SimPiano）编码格式

```
[速度BPM] 音符序列

音符格式：[*][1-7][*][#|!][~]
  * 在数字前 = 降八度  * 在数字后 = 升八度
  # = 升半音    ! = 降半音
  ~ = 延长 1 拍
休止符：-      延长休止：-~
分组：(音符1 音符2)  = 等分时值
延长分组：(~音符1 音符2)  = 前音符延长后与组内等分剩余拍

示例：[120] (1 3 5) 6~ 5 3 1--
```

---

## 📦 关键数据结构

### DecisionRules (`config/decision_rules.json`)
- `context_max_messages`: 30
- `density_high_threshold`: 10（条/分钟）
- `reply_threshold_default`: 0.6
- `classifier_fallback_threshold`: 0.45
- `failover.retry_interval_seconds`: 1800（30分钟）
- 错误关键词：21个（中英文）

### 可用模型（`decision_engine.AVAILABLE_MODELS`）
```
deepseek-v4-flash-ascend, glm-5.2, deepseek-v4-pro,
qwen3.6-chat, qwen3.6-reasoner
```

### 重要代码常量

| 常量 | 值 | 位置 |
|------|-----|------|
| `BOT_QQ` | `"2668851638"` | command_handler.py |
| `VERSION` | `"2.2.3"` | command_handler.py |
| `HOST` | `"0.0.0.0"` | reverse_bot.py |
| `PORT` | `8080` | reverse_bot.py |
| `PROCESSED_MSG_MAX` | `200` | command_handler.py |
| `YAU_MAX_USAGE` | `3` / 小时 | command_handler.py |
| `ZHOULI_RATE_LIMIT` | `3` / 小时 | command_handler.py |
| `PIXEL_SIZE` | `32` | command_handler.py |
| `YIJING_DATA_FILE` | `yijing_structured_fixed.json` | command_handler.py |
| `SCRIPT_DIR` | `os.path.dirname(__file__)` | 各文件自行计算 |
| `NAPCAT_HTTP_URL` | `http://127.0.0.1:6099` | command_handler.py |

---

## 🔧 常见问题排查

### 1. WS 断连
日志显示 `💀 连接断开: 僵尸连接被心跳检测发现` → NapCat 仍运行但 TCP 已失效。
解决：NapCat WebUI 重连或重启 NapCat。

### 2. 决策引擎不回复
检查：
- `config/group_features.json` 中 `decision_engine = true`？
- `config/decision_rules.json` 密度/冷却阈值是否合理？
- `.env` 中 `DECISION_API_KEY` 是否有效？

### 3. API Key 过期
```bash
# 测试 API 连通性
@机器人 test
```

### 4. 日志文件
所有日志在 `logs/bot.YYYY-MM-DD.log`，保留 30 天。
日志解析工具：`python analyze_model_success.py`

---

## 📜 版本简史

| 版本 | 关键变更 |
|------|----------|
| 1.0.0 | 初始：菜单系统 |
| 1.6.0 | 决策引擎 + 用户画像 |
| 2.0.0 | 指令逻辑拆出为 command_handler.py（精简 reverse_bot.py 从 2395→478 行）|
| 2.0.1 | API Key 全部迁移至 .env |
| 2.1.0 | 多群多角色多记忆 |
| 2.2.0 | 周礼模块 |
| 2.2.3 | 画像超时 20s→40s，移除标点校验 |

