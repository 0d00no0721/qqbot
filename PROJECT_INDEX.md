# PROJECT_INDEX — QQbot 项目速览

📌 **项目定位**：基于 OneBot V11 协议的 QQ 群聊机器人，NapCat 客户端 + Python WebSocket 服务端，集成多模型决策引擎实现上下文驱动的智能回复。

🛠 **技术栈**：Python 3 · websockets · httpx · sentence-transformers · DeepSeek/Qwen/GLM API · Pillow · numpy · zhdate · jmcomic

---

## 📁 目录结构与职责

| 目录/文件 | 职责 |
|-----------|------|
| `reverse_bot.py` | **主机器人** WS 服务端入口（555 行，仅核心逻辑） |
| `standalone_bot.py` | 备选 WS 客户端启动 |
| `auto_config.py` | NapCat WS 连接自动配置 |
| `set_env.py` | 设置 HF_HOME / TRANSFORMERS_CACHE |
| `config/` | 决策规则、人设（五段式）、角色库 |
| `scripts/` | 决策引擎、分类器、画像、命令处理器等 |
| `archive/` | 归档的废弃文件/项目/临时文件 |
| `NapCat/` | QQ NT 客户端本体 |
| `models_cache/` | HuggingFace 模型缓存 |
| `venv/` | Python 虚拟环境 |
| `logs/` | 运行日志输出 |

### scripts/ 核心模块

| 文件 | 职责 |
|------|------|
| `command_handler.py` | **命令处理器** — 所有指令处理逻辑（~2140 行） |
| `decision_engine.py` | 上下文驱动发言决策 + 角色 Persona/记忆加载 + 故障转移（~1270 行） |
| `model_primary.py` | 主模型子进程（中科大代理，OpenAI 协议） |
| `model_fallback.py` | 备用模型子进程（DeepSeek 官方） |
| `zero_shot_classifier.py` | 零样本分类器（sentence-transformers，按需加载） |
| `reply_generator.py` | DeepSeek API 封装 |
| `user_profile.py` | 用户画像生成（缓冲+定时API） |
| `memory_manager.py` | 角色记忆+人设管理交互式终端（~670 行） |
| `sign_in.py` | 签到+排名+连续天数 |
| `mystery_number.py` | JMComic ID 验证 |
| `nekosia_image.py` | 猫娘图片（Nekosia API） |
| `pixiv_helper.py` | Pixiv 排行榜图片（PHPSESSID 鉴权） |
| `download_model.py` | HF 模型预下载 |

### config/ 配置

| 文件 | 职责 |
|------|------|
| `decision_rules.json` | 决策引擎参数+故障转移配置 |
| `persona.txt` | 默认人设（五段式：核心身份/语言风格/行为准则/情绪反应/互动策略） |
| `active_character.json` | 角色开关+管理员配置 |
| `group_characters.json` | 群→角色映射 |
| `active_model.json` | 当前激活的主模型 |
| `characters/` | 角色库（每个角色一个子目录） |
| └─ `{角色}/persona.txt` | 角色专属人设（五段式） |
| └─ `{角色}/info.json` | 角色名 + 别名 |
| └─ `{角色}/memories/character/default.json` | 角色通用记忆（6 类：身份/关系/信念/知识/经历/偏好） |
| └─ `{角色}/memories/group/{gid}.json` | 群专属记忆 |

### 角色系统设计原则

```
Persona（五段式）→ 「怎么做」：语言风格、情绪模式、行为边界、互动策略
Memories（六分类）→ 「知道什么」：身份事实、关系网络、世界观知识、关键经历
两者互补，Persona 是行为编译器（LLM 优先遵循），Memories 是知识数据库（LLM 参考使用）
```

System prompt 组装顺序：**Persona → 指令强化分隔符 → 角色通用记忆 → 群专属记忆 → 群友画像**，权重从前到后递减。

---

## ⚡ 常用命令

```bash
# 启动主机器人（WS 服务端监听 8080）
PYTHONIOENCODING=utf-8 ".\venv\Scripts\python" reverse_bot.py

# 备选启动（WS 客户端连接 NapCat:6700）
PYTHONIOENCODING=utf-8 python standalone_bot.py

# 角色记忆/人设管理
PYTHONIOENCODING=utf-8 python scripts/memory_manager.py

# 预下载分类模型
PYTHONIOENCODING=utf-8 ".\venv\Scripts\python" scripts/download_model.py
```

---

## 📌 当前状态

- **版本**：2.2.4（Persona 五段式重构）
- **活跃模型**：`deepseek-v4-pro`（中科大代理）
- **角色**：`soyo nagasaki`（长崎素世，已启用）
- **群角色映射**：`group_characters.json` → 群 755471390 绑定 soyo
- **记忆结构**：`memories/character/default.json`（通用，~8KB）+ `memories/group/{gid}.json`（群专属）
- **管理员**：`784427550`
- **帮助系统**：分层帮助（`帮助` / `[类别]帮助`）
- **角色管理指令**：`查看人设` / `设置人设` / `添加记忆` / `删除记忆` / `查看记忆` / `切换角色` / `角色列表` / `启用角色` / `停用角色`

---

📅 **最后更新**：2026-07-10