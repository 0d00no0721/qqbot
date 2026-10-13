# QQbot

基于 **OneBot V11** 协议的 QQ 群聊机器人。它不是"指令应答机"，而是一套**上下文驱动的 LLM 发言决策系统**：自己判断该不该插话、用哪个模型说、以什么角色说。

- NapCat（QQ NT 客户端）+ Python 反向 WebSocket 服务端
- 三层漏斗决策：硬规则 → 消息密度 → 零样本语义分类
- 两层模型熔断：主子进程隔离 → 5 档 9 模型优先级池
- 五段式 Persona + 六分类记忆的角色系统
- Windows 部署，Python 3.10+

```
QQ 群
  │
NapCat（QQ NT 协议端）
  │  反向 WS 连入 :8080
  ▼
reverse_bot.py            入口：WS 服务端 + 消息路由器
  ├─ scripts/commands/    指令层（注册表模式，33 条指令）
  ├─ scripts/decision_engine.py   决策层：要不要接话
  │     └─ subprocess ─→ model_primary.py / model_fallback.py
  │                          └─ scripts/llm_pool.py   模型层：本地网关模型池
  ├─ scripts/user_profile.py      用户画像（异步旁路）
  └─ scripts/zero_shot_classifier.py  零样本分类（懒加载）
```

---

## ✨ 关键设计

### 发言决策：三层漏斗（成本从低到高）

| 层 | 手段 | 成本 |
|---|---|---|
| 硬规则 | `@bot` 必回；长度 < 3、纯 emoji 不回；同群 10s 冷却 | 0 |
| 统计 | 消息密度（条/分钟），> 15 时放弃（刷屏不添乱） | 0 |
| 语义 | sentence-transformers 零样本分类「需要回复 / 不需要回复」 | 一次 embedding |

动态阈值：群聊越热闹阈值越低（更敢接话），越冷清阈值越高（更克制）。分类器置信度 < 0.45 时**不盲目沉默**，改由密度决策——群里没人说话还装死，机器人就"死"了。

### 模型池：四类错误分流

单一端点挂了机器人就哑了，因此 `scripts/llm_pool.py` 实现了一条优先级故障转移链（`config/model_pool.json`）：

```
T1 deepseek-flash, deepseek-flash-2
T2 deepseek-v4-flash-ascend, deepseek-v4-flash-ascend1
T3 qwen3.8-flash-next-2, qwen3.8-flash-next
T4 glm-5.3-flash
T5 qwen3.8-reasoner, qwen-reasoner
```

档内按序试，某档全挂才降档。错误按其**真实语义**分流，而不是一律重试：

| 类别 | 触发 | 处置 | 理由 |
|---|---|---|---|
| `auth` | 401 / 鉴权语义 403 | **立即中止整池**，不冷却 | 全局故障，重试只烧预算 |
| `request` | 参数/校验类 400 | 不冷却，换下一个 | 一次坏请求不能冻死整池 |
| `model_missing` | 404 / "模型不存在" | 长冷却 600s | 该模型确实不可用 |
| `transient` | 超时 / 429 / 5xx / 空内容 | 短冷却 60s | 可能是抖动 |

时间预算必须互相咬合：**单次 20s → 总预算 45s → 子进程超时 60s**，保证池永远来得及给上层一个明确失败。

两个踩坑修正（都有实测依据）：
- **`max_tokens` 下限 512**：思考型模型 token 给小了会 `HTTP 200 + content 空`，看起来像模型故障。现网有 `max_tokens=10/100/150` 的调用点，不做下限则故障转移形同虚设。
- **`response_format` 按模型关闭**：`qwen3.8-flash-next` 会把 JSON 塞进 `reasoning_content`、`content=null`。

### 角色系统：Persona 与 Memories 分离

```
Persona（五段式）  → 「怎么做」：语言风格、情绪模式、行为边界、互动策略
Memories（六分类） → 「知道什么」：身份/关系/信念/知识/经历/偏好
```

System prompt 组装顺序（权重递减）：**角色 persona → 指令强化分隔符 → 角色通用记忆 → 群专属记忆 → 群友画像**。

中间那句分隔符是防"记忆污染人设"的关键——不写它，LLM 会把记忆里的知识当成新的行为指令。

内容安全**完全由代码层执行**（关键词表 + 长度 + 乱码检测），不依赖 LLM 自我审查：LLM 的自我审查不可验证，代码里的规则表才可审计。

### 指令系统：注册表模式

`scripts/command_handler.py` 只是 72 行门面，真正的分发在 `scripts/commands/`：

```python
@register("周礼", match_type="prefix")
async def cmd_zhouli(ctx: CommandContext) -> bool:
    await ctx.send("礼官正在斟酌，请稍候~", at_user=ctx.uid_str)
    ...
    return True          # True = 已处理，停止后续匹配
```

匹配类型：`exact` / `prefix` / `substring` / `regex` / `custom`。加指令只需两步：写模块 + 在 `commands/__init__.py` 里 import。

---

## 🚀 快速开始

```powershell
cd E:\QQbot

# 1. 依赖
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
# pixivtools 不是 pip 包，需本地安装（Pixiv 功能需要）：
#   cd pixivtools-src\pixivtools-main && pip install . && cd ..\..

# 2. 配置密钥
copy .env.example .env      # 然后填入你自己的 key

# 3. 预下载分类模型（首次运行会自动下载，也可提前下）
.\venv\Scripts\python scripts\download_model.py

# 4. 启动 NapCat（QQ NT 客户端，需先登录）
start "" "NapCat\NapCat.44498.Shell\NapCatWinBootMain.exe"

# 5. 启动机器人（WS 服务端监听 :8080）
$env:PYTHONIOENCODING="utf-8"
.\venv\Scripts\python reverse_bot.py
```

然后在 NapCat WebUI 中添加 WebSocket **客户端**，地址 `ws://127.0.0.1:8080`。

> `$env:PYTHONIOENCODING="utf-8"` 不是可选项——Windows 中文环境下不加它，Python 输出中文会崩。

### 验证改动（本项目没有 pytest）

```powershell
# 事实上的"测试"：导入自检
$env:PYTHONIOENCODING="utf-8"; .\venv\Scripts\python -c "import reverse_bot; print('OK')"

# 模型池自检
.\venv\Scripts\python scripts\llm_pool.py
```

`scripts/test_*.py` 是探索性/演练脚本，不是测试套件。

### 管理工具

```powershell
.\venv\Scripts\python scripts\group_manager.py     # 群功能开关（交互式 CLI）
.\venv\Scripts\python scripts\memory_manager.py    # 角色记忆/人设管理
```

---

## 📁 目录结构

| 路径 | 职责 |
|---|---|
| `reverse_bot.py` | 入口：WS 服务端 + 消息路由（去重/白名单/语音转写/别名匹配） |
| `scripts/decision_engine.py` | 决策引擎：上下文缓冲、密度阈值、话题切换检测、主备熔断 |
| `scripts/llm_pool.py` | **中央模型池**：优先级链、冷却、预算、审计 JSONL |
| `scripts/commands/` | 指令包（注册表模式，14 个模块） |
| `scripts/model_primary.py` / `model_fallback.py` | 模型子进程（stdin/stdout JSON 通信） |
| `scripts/zero_shot_classifier.py` | 零样本分类器（懒加载，避免启动时载入 torch） |
| `scripts/user_profile.py` | 用户画像（缓冲 + 定时批量更新） |
| `scripts/group_manager.py` | 群功能管理 CLI |
| `scripts/memory_manager.py` | 记忆/人设管理 CLI |
| `config/` | 全部可热改配置（JSON） |
| `data/` | 静态数据：`yijing_structured_fixed.json`（算卦六十四卦）、`yau_style.txt`（Yau 风格指令） |
| `docs/` | 文档：`PROJECT_INDEX.md`（项目速览）、`VERSION_HISTORY.md`（版本史）、`SimPiano.md`（钢琴曲谱规范） |
| `tools/` | 本机运维脚本（NapCat verbose 启动、bypass 取证），**不入库** |
| `qq_native_re/` | 工程档案：决策日志 + 实测报告（见该目录 README） |
| `NapCat/` `venv/` `models_cache/` `logs/` `pixivtools-src/` | 依赖与运行时目录，**不入库** |

> 运行时数据文件保持在仓库根目录（`menu_data.json` / `user_profiles.json` / `scripts/sign_in_data.json` / `.env`），代码按根路径读取；它们已在 `.gitignore` 中排除。

---

## ⚙️ 配置说明

### 环境变量（`.env`）

| 变量 | 说明 |
|---|---|
| `LLM_POOL_BASE_URL` | 模型网关地址，默认 `http://127.0.0.1:8090/v1` |
| `LLM_POOL_API_KEY` | 网关密钥 |
| `DEEPSEEK_API_KEY` / `DECISION_API_KEY` / `DIVINATION_API_KEY` | 历史遗留键名，保留作回滚锚点 |

> 本项目**不保留云端兜底**：所有模型调用只走本地网关。你可以用 LiteLLM / one-api / new-api 之类的网关在 `:8090` 上聚合多个上游，本项目的优先级链与冷却逻辑全部在网关这一侧生效。

### 配置文件（`config/`）

| 文件 | 用途 | 热加载 |
|---|---|---|
| `model_pool.json` | 模型优先级链、超时、冷却、`max_tokens` 下限 | ✅ mtime |
| `active_model.json` | 当前主模型（必须在链内） | ✅ |
| `active_character.json` | 角色开关 + `admin_ids` | ✅ |
| `group_characters.json` | 群 → 角色映射 | ✅ |
| `group_features.json` | 群 → 五项功能开关 | ✅ mtime |
| `decision_rules.json` | 决策阈值 + 内容校验规则 | 重启 |
| `characters/{角色}/` | `persona.txt`（五段式）+ `info.json` + `memories/` | ✅ |

⚠️ **`group_features.json` 是白名单语义**：未配置的群 **五项功能全关**（不是全开）。新群必须先用 `group_manager.py` 加入，否则机器人完全静默且不打日志。

---

## 💬 指令一览

发送 `@bot 帮助` 查看分类菜单，`@bot [类别]帮助` 查看详情。

| 类别 | 指令 |
|---|---|
| 菜单 | `添加菜单/加菜`、`删除菜单/删菜`、`查看菜单`、`今天吃啥 [自己\|群]`、`选择 a b c` |
| 小巧思 | `Yau <话>`、`周礼 <文字>`、`加密/解密`（咕嘎比特密文）、`算卦`、`猫娘`、`pixiv随机日/周/月榜`、`像素画`、`钢琴 <曲谱>` |
| 角色 | `查看人设`、`设置人设`、`添加记忆`、`删除记忆`、`查看记忆`、`切换角色`、`角色列表`、`启用/停用角色` |
| 系统 | `切换模型 <名>`、`查看模型`、`test`（模型池连通性体检） |
| 日常 | `签到`、`神秘数字` |

`@bot` 之外，机器人还会按**角色别名**触发（如 `大肥鱼`、`soyo`），以及可选的 `+1` 复读、新人欢迎。

---

## 📌 工程档案

`qq_native_re/` 是本项目的特色：每个反直觉的实现细节背后都有一份实测报告。

- `decisions/` — 8 份决策日志（做什么、为什么、如何回滚、遗留待办）
- `findings/` — 模型池探针（PROBE.md）、故障演练（H1~H6）、回放对比、各 track 报告

典型例子：`404` 响应体会回显上游多 key 的内部人名，所以审计日志只落 `error.type + message≤200字` 并打码 `sk-*`；两种 400 的 `error.type` 实测都是 `invalid_request_error`（差别在 message），所以分流判据必须 type + message 双判。

---

## ⚠️ 已知限制

- **Windows only**：路径用 `\`，依赖 `.exe`；`PYTHONIOENCODING=utf-8` 必需
- **必须由普通控制台启动**：受限环境（沙箱）下 `asyncio.create_subprocess_exec` 会 `WinError 5`，且无法写 `logs/`，表现为"在线但永不回复"
- **模型网关需自行部署**：`127.0.0.1:8090` 不是本项目提供的组件
- `logs/` 若 ACL 异常，日志会降级为仅控制台输出（这是刻意设计：日志打不开绝不阻断启动）
- 无自动化测试套件；验证靠导入自检 + 定向 dispatch 测试

---

## 📄 许可

个人项目，仅供学习参考。角色设定与人格文本归原作者/版权方所有。

> 本仓库不包含任何真实群号、用户 QQ 号、聊天记录与 API 密钥；运行时数据文件（`menu_data.json`、`user_profiles.json`、`logs/` 等）已在 `.gitignore` 中排除。
