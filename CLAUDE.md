# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

QQ 群机器人项目。基于 OneBot V11 协议，使用 NapCat 作为 QQ 客户端。群号 `755471390`，机器人 QQ `2668851638`。部署在 Windows 环境。

v2.0.0 重构：指令逻辑已从 `reverse_bot.py`（483 行）拆出到 `scripts/command_handler.py`（~2160 行）。`reverse_bot.py` 仅保留 WS 服务端、决策引擎调用、+1 复读、后台任务。

v2.3.0 新增虚拟股市板块：`scripts/virtual_stock/` 独立包，8 支股票 × 群聊指标定价，AMM 做市商，做多/做空/3倍杠杆/熔断/拆股/分红，一群一盘独立隔离。群主 QQ `408754232` 硬编码。

决策引擎通过子进程调用模型脚本，多模型故障转移，故障转移和用户画像模块均走 OpenAI 协议（`/v1/chat/completions`）。

## Commands

```bash
# 启动 NapCat（需要 Powershell / cmd，此 shell 不支持）
start "" "E:\QQbot\NapCat\NapCat.44498.Shell\NapCatWinBootMain.exe"

# 启动 reverse_bot（主机器人，WS 服务端监听 8080，必须 venv 环境）
cd E:\QQbot && PYTHONIOENCODING=utf-8 ".\venv\Scripts\python" reverse_bot.py

# 启动 standalone_bot（备选，WS 客户端连接 NapCat:6700，仅菜单功能）
cd E:\QQbot && PYTHONIOENCODING=utf-8 python standalone_bot.py

# 角色记忆管理交互式终端
cd E:\QQbot && PYTHONIOENCODING=utf-8 python scripts/memory_manager.py

# 预下载分类模型
cd E:\QQbot && PYTHONIOENCODING=utf-8 ".\venv\Scripts\python" scripts/download_model.py
```

## Port Allocation

| 端口 | 角色 |
|------|------|
| 8080 | reverse_bot 监听，NapCat 主动连接 |
| 6700 | NapCat WS 服务端，standalone_bot 连接 |
| 6099 | NapCat HTTP API 管理接口 |

## WS Connection

- `reverse_bot.py`: WS 服务端监听 8080，NapCat 配置 `websocketClients` 主动连接。心跳 30s ping / 10s timeout 检测僵尸连接。
- `standalone_bot.py`: WS 客户端模式，主动连接 NapCat 6700。

## Architecture

```
E:\QQbot/
├── reverse_bot.py                # 主入口 — WS 服务端（483 行，仅核心逻辑）
├── standalone_bot.py             # 备选 — WS 客户端，仅菜单
├── auto_config.py                # NapCat WS 连接自动配置
├── set_env.py                    # 设置 HF_HOME / TRANSFORMERS_CACHE
├── VERSION_HISTORY.md            # 版本更新记录
├── memory_manager_guide.md       # 记忆管理使用指南
├── multi_group_character_plan.md # 多群多角色改造方案文档
│
├── scripts/
│   ├── command_handler.py        # 命令处理器 — 所有指令处理逻辑（1883 行）
│   ├── decision_engine.py        # 决策引擎（子进程隔离、故障转移、角色注入，1251 行）
│   ├── model_primary.py          # 主模型子进程（中科大代理，OpenAI 协议）
│   ├── model_fallback.py         # 备用模型子进程（DeepSeek 官方，OpenAI 协议）
│   ├── zero_shot_classifier.py   # 零样本分类器（sentence-transformers，按需加载）
│   ├── reply_generator.py        # DeepSeek API 封装
│   ├── user_profile.py           # 用户画像（缓冲+定时API，故障转移）
│   ├── memory_manager.py         # 角色记忆管理交互式终端
│   ├── sign_in.py                # 签到+排名+连续天数
│   ├── mystery_number.py         # JMComic ID 验证
│   ├── nekosia_image.py          # 猫娘图片（Nekosia API）
│   ├── pixiv_helper.py           # Pixiv 排行榜图片（PHPSESSID 鉴权）
│   ├── download_model.py         # HF 模型预下载
│   ├── virtual_stock/            # 虚拟股市板块（独立包）
│   │   ├── __init__.py           # 包入口 — 对外API（on_message/handle_vs_command/start_scheduler）
│   │   ├── data.py               # 数据层 — 群隔离、账户、股价持久化
│   │   ├── engine.py             # 定价引擎 — 8股各自算法（群主发言占比/水群频率/关键词匹配等）
│   │   ├── account.py            # 账户管理 — 余额/体力/杠杆/做空/破产恢复
│   │   ├── market.py             # 交易系统 — AMM做市商/买卖/手续费/熔断检查
│   │   ├── risk.py               # 风控 — 保证金率/爆仓强平/熔断
│   │   ├── events.py             # 事件 — 拆股/分红/收盘/富豪榜
│   │   ├── commands.py           # 指令处理 — 行情/买入/卖出/做空/持仓等
│   │   ├── scheduler.py          # 定时任务 — 8个后台协程（股价刷新/爆仓/拆股/体力/日息/榜单/收盘/分红）
│   │   ├── DESIGN.md             # 设计文档
│   │   └── data/                 # 运行时数据（按群隔离）
│   └── test_*.py                 # 测试脚本
│
├── config/
│   ├── decision_rules.json       # 决策引擎参数 + 故障转移配置
│   ├── persona.txt               # 默认人设
│   ├── active_model.json         # 当前激活的主模型
│   ├── active_character.json     # 角色开关 + 管理员配置
│   ├── group_characters.json     # 群→角色映射
│   ├── labels.json               # 旧版分类标签
│   └── characters/               # 角色库（每个角色一个子目录）
│       └── soyo nagasaki/
│           ├── info.json         # 角色名 + 别名
│           ├── persona.txt       # 角色人设
│           └── memories/
│               ├── character/    # 角色通用记忆（memory_manager.py 管理）
│               │   └── default.json
│               └── group/        # 群专属记忆（@bot 添加记忆 产生）
│                   └── 755471390.json
│
├── archive/                      # 归档的废弃文件/项目/临时文件
├── yijing_structured_fixed.json  # 算卦数据（卦名、卦辞、爻辞）
├── yau_style.txt                 # Yau 命令人设
├── SimPiano.md                   # 钢琴曲谱格式文档
├── menu_data.json                # 菜单数据
├── user_profiles.json            # 用户画像
├── sign_in_data.json             # 签到数据（scripts/ 目录下）
├── .env                          # API Key 等凭据（不提交）
├── .env.example                  # 凭据模板
├── models_cache/                 # HF 模型缓存
└── logs/                         # 运行日志
```

## API Keys

所有 API Key 从 `.env` 读取（`reverse_bot.py` 通过 `load_dotenv()` 加载）：

| 用途 | 环境变量 | 端点 |
|------|----------|------|
| 决策引擎主模型 | `DECISION_API_KEY` | 中科大代理 `api.llm.ustc.edu.cn/v1` |
| Yau / 决策引擎备用 | `DEEPSEEK_API_KEY` | DeepSeek 官方 `api.deepseek.com/v1` |
| 算卦解读 | `DIVINATION_API_KEY` | DeepSeek 官方 `api.deepseek.com/v1` |

`.env` 不提交到 Git，通过 `.env.example` 参考。

## Decision Engine (scripts/decision_engine.py)

消息处理流水线：
1. 写上下文环形缓冲区（每群最多 50 条）
2. @bot → 强制回复（最高优先级）
3. 前置过滤：过短(<3) / 纯 emoji → 跳过
4. 冷却检查：同群间隔 <10s → 跳过
5. 密度检查：>15 条/分钟 → 跳过
6. 零样本分类 + 动态密度阈值
7. 分类器降级（<0.45 置信度时纯密度决策）
8. 生成回复（子进程）→ 内容安全过滤 → 精确去重 → 相似度去重

模型调用通过子进程（subprocess）隔离，主模型 `model_primary.py` + 备用 `model_fallback.py`，均走 OpenAI 协议。

## Command Handler (scripts/command_handler.py)

2.0.0 新增。通过 `init_handlers()` 接收 `reverse_bot.py` 的日志器、决策引擎和发送函数引用。`handle_command()` 为统一入口，返回 True/False 表示是否匹配指令。

## Zhouli (周礼)

2.2.0 新增。`@bot 周礼 <文字>` 将大白话改写成"合乎周礼"白话翻译腔。System Prompt 源自 `zhouli-translator-ref` 项目（`Aspirin0000/zhouli-translator`），在此基础上简化了 user prompt（AI 自动选择辞气）。API 调用优先中科大代理（`DECISION_API_KEY`），失败后切 DeepSeek 官方（`DEEPSEEK_API_KEY`）。

## 多群多角色

- `config/group_characters.json`：群→角色映射，未配置的群回退 `active_character.json` 全局默认
- `config/characters/{角色}/memories/character/default.json`：角色通用记忆
- `config/characters/{角色}/memories/group/{gid}.json`：群专属记忆
- `@bot 添加记忆` 写入群记忆，`@bot 查看记忆` 发送合并文件
- `memory_manager.py` 管理角色通用记忆
- 别名检测覆盖所有已分配角色的别名

## Help System

分层帮助：
- `帮助` / `help` → 类别一行式概览
- `[类别]帮助` → 子类别详细命令列表
- 类别：菜单、小巧思、角色、系统、日常、股市

## Virtual Stock (scripts/virtual_stock/)

v2.3.0 新增虚拟股市板块。独立 Python 包，按群隔离（一群一盘）。

- **8 支股票**：600001 群主控股（群主发言占比指标）、300001 水群地产、300002 情绪过山车、30003A/30003B CP双子星、000001 潜水者指数、100001 战雷航空、100002 军武游戏、900001 周末效应
- **群主 QQ**：`408754232`，硬编码在 `data.py` 常量 `OWNER_QQ`
- **AMM 做市商**：买卖价差 1%（买价=现价×1.005, 卖价=现价×0.995）
- **交易机制**：做多/做空/3倍杠杆+爆仓/体力值（上限10，30min恢复1点）
- **风控**：单股1小时涨跌超30%或大盘跌超15%触发熔断；保证金率≤10%强平
- **定时任务**：调度器8个协程 — 股价刷新(10min)/爆仓检查(10min)/拆股检查(10min)/体力恢复(30min)/日息(00:00)/富豪榜(00:05)/收盘(23:30)/周分红(周日22:00)
- **数据隔离**：`scripts/virtual_stock/data/group_{群号}/` 目录，config.json + prices.json + accounts/{user_id}.json
- **集成方式**：
  - `reverse_bot.py` 导入 `on_message` 采集所有群消息指标，启动调度器
  - `command_handler.py` 在 `handle_command()` 开头检查 `_vs_is_command()` 分发虚拟股指令
  - 调度器广播通过 `current_websocket` 发送群消息

## Active Model

当前配置：`deepseek-v4-pro`（中科大代理）
可用模型：`deepseek-v4-flash-ascend`, `glm-5.2`, `deepseek-v4-pro`, `qwen3.6-chat`, `qwen3.6-reasoner`

## Version Convention

`VERSION` 遵循 `主版本.次版本.修订号`。当前 `2.3.0`。