# PROJECT_INDEX — QQbot 项目速览

📌 **项目定位**：基于 OneBot V11 协议的 QQ 群聊机器人，NapCat 客户端 + Python WebSocket 服务端，集成多模型决策引擎实现上下文驱动的智能回复。

🛠 **技术栈**：Python 3 · websockets · httpx · sentence-transformers · 本地网关模型池（OpenAI 协议）· Pillow · numpy · zhdate · jmcomic

---

## 📁 目录结构与职责

| 目录/文件 | 职责 |
|-----------|------|
| `reverse_bot.py` | **主机器人** WS 服务端入口（536 行，仅核心逻辑） |
| `set_env.py` | 设置 HF_HOME / TRANSFORMERS_CACHE |
| `config/` | 决策规则、人设（五段式）、角色库、**模型池配置** |
| `scripts/` | 决策引擎、模型池、分类器、画像、指令包等 |
| `qq_native_re/` | 工程档案：决策日志 + 实测报告（见该目录 README） |
| `archive/` | 归档的废弃文件/项目/临时文件（gitignore） |
| `NapCat/` | QQ NT 客户端本体（gitignore） |
| `models_cache/` | HuggingFace 模型缓存（gitignore） |
| `venv/` | Python 虚拟环境（gitignore） |
| `logs/` | 运行日志输出（gitignore） |

> 历史文件 `standalone_bot.py`（备用 WS 客户端）、`auto_config.py`（NapCat 自动配置）、`scripts/virtual_stock/`（虚拟股市）已在模型池迁移时移除，仅存于 git 历史。

### scripts/ 核心模块

| 文件 | 职责 |
|------|------|
| `command_handler.py` | **指令门面**（~72 行）— 构建 CommandContext + 委托 `commands/_registry.dispatch` |
| `commands/` | **指令包**（注册表模式，14 模块 / 33 条指令）— `@register` 声明，`__init__.py` 导入即注册 |
| `decision_engine.py` | 上下文驱动发言决策 + 角色 Persona/记忆加载 + 故障转移（~1420 行） |
| `llm_pool.py` | **中央模型池**（~1100 行）— 唯一模型出口：优先级链故障转移、四类错误分流、冷却、预算、审计 JSONL |
| `model_primary.py` | 主模型子进程（stdin/stdout JSON 通信） |
| `model_fallback.py` | 备用模型子进程（与主模型完全隔离） |
| `zero_shot_classifier.py` | 零样本分类器（sentence-transformers，懒加载） |
| `user_profile.py` | 用户画像生成（缓冲+定时 API） |
| `memory_manager.py` | 角色记忆+人设管理交互式终端（~580 行） |
| `group_manager.py` | 群功能开关管理 CLI（~510 行） |
| `vision.py` / `image_utils.py` / `image_cache.py` | 视觉与图片处理 |
| `sign_in.py` | 签到+排名+连续天数 |
| `mystery_number.py` | JMComic ID 验证 |
| `nekosia_image.py` | 猫娘图片（Nekosia API） |
| `pixiv_helper.py` | Pixiv 排行榜图片（PHPSESSID 鉴权） |
| `download_model.py` | HF 模型预下载 |

### config/ 配置

| 文件 | 职责 |
|------|------|
| `model_pool.json` | **模型池优先级链**（5 档 9 模型）+ 超时/冷却/max_tokens 下限（mtime 热加载） |
| `decision_rules.json` | 决策引擎参数 + 内容校验规则（长度/乱码/错误关键词） |
| `group_features.json` | 群→功能开关（**白名单语义**：未配置的群全关） |
| `persona.txt` | 默认人设（五段式：核心身份/语言风格/行为准则/情绪反应/互动策略） |
| `active_character.json` | 角色开关 + 管理员配置 |
| `group_characters.json` | 群→角色映射（缺失时回退全局默认角色） |
| `active_model.json` | 当前激活的主模型（仅 `model` 字段；端点/密钥由模型池决定） |
| `labels.json` | 分类器候选标签 |
| `characters/` | 角色库（每个角色一个子目录） |
| └─ `{角色}/persona.txt` | 角色专属人设（五段式） |
| └─ `{角色}/info.json` | 角色名 + 别名 |
| └─ `{角色}/memories/character/default.json` | 角色通用记忆（6 类：身份/关系/信念/知识/经历/偏好，带 `source` 溯源） |
| └─ `{角色}/memories/group/{gid}.json` | 群专属记忆（运行时真实数据，gitignore） |

### 角色系统设计原则

```
Persona（五段式）→ 「怎么做」：语言风格、情绪模式、行为边界、互动策略
Memories（六分类）→ 「知道什么」：身份事实、关系网络、世界观知识、关键经历
两者互补，Persona 是行为编译器（LLM 优先遵循），Memories 是知识数据库（LLM 参考使用）
```

System prompt 组装顺序：**Persona → 指令强化分隔符 → 角色通用记忆 → 群专属记忆 → 群友画像**，权重从前到后递减。

---

## ⚡ 常用命令

```powershell
# 启动主机器人（WS 服务端监听 8080；NapCat 反向连入）
python -m venv venv && .\venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # 填入 LLM_POOL_BASE_URL / LLM_POOL_API_KEY 等
$env:PYTHONIOENCODING="utf-8"; .\venv\Scripts\python reverse_bot.py

# 导入自检（本项目没有 pytest，这是事实上的验证手段）
$env:PYTHONIOENCODING="utf-8"; .\venv\Scripts\python -c "import reverse_bot; print('OK')"

# 模型池自检（不依赖机器人进程）
.\venv\Scripts\python scripts\llm_pool.py

# 群功能开关管理
.\venv\Scripts\python scripts\group_manager.py

# 角色记忆/人设管理
.\venv\Scripts\python scripts\memory_manager.py

# 预下载分类模型
.\venv\Scripts\python scripts\download_model.py
```

---

## 📌 当前状态

- **版本**：`2.5.0`（`scripts/commands/_state.py`）；同步打 tag `v2.5.0`（联网搜索 + 回复质量系列）
- **模型出口**：本地网关 `http://127.0.0.1:8090/v1`（**唯一出口，无云端兜底**）
- **主模型**：`deepseek-flash`（`config/active_model.json`，必须在池内优先级链中）
- **优先级链**：5 档 9 模型（deepseek-flash → deepseek-v4-flash-ascend → qwen3.8-flash-next → glm-5.3-flash → qwen3.8-reasoner）
- **角色**：`大肥鱼`（`active_character.json` 全局启用），群 `284517232` 绑定同一角色
- **记忆结构**：`memories/character/default.json`（通用）+ `memories/group/{gid}.json`（群专属，运行时数据不入库）
- **帮助系统**：分层帮助（`帮助` / `[类别]帮助`），类别：菜单、小巧思、角色、系统、日常
- **角色管理指令**：`查看人设` / `设置人设` / `添加记忆` / `删除记忆` / `查看记忆` / `切换角色` / `角色列表` / `启用角色` / `停用角色`
- **模型池观测**：`logs/llm_pool.jsonl` 审计流水（每次尝试一行 JSON，含 tier/model/latency/tokens）；`@bot test` 查看池状态与冷却快照
- **虚拟股市**：已在本次同步中移除（原实现仅存于 git 历史）

---

📅 **最后更新**：2026-10-12