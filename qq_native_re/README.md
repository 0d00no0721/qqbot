# qq_native_re — 工程档案

这个目录不是运行代码，是**项目的工程方法记录**：每个反直觉的实现细节背后，都有一份实测报告和一次带回滚方案的决策。

## 目录说明

| 路径 | 内容 |
|---|---|
| `decisions/` | 决策日志（编号 001~008）。每份记录：做了什么、实测证据、过程中的裁决、回滚方法、遗留待办 |
| `findings/model_pool/` | 模型池完整证据链：`PROBE.md`（能力探针）、`W1~W6` 各阶段报告 |
| `findings/reply_fix/` | 定向回复改造的回放对比（修前/修后行为基线） |
| `findings/track_A~G/` | 各专题调查报告 |
| `findings/vision/` | 视觉模块接线报告 |
| `findings/observation_window/` | 观察窗记录 |
| `prompts/` | 各 track 的原始任务书 |

## 什么进仓库、什么不进

**进仓库**：`.md` 报告与决策日志——结论、证据摘要、可复现命令。

**不进仓库**（见根目录 `.gitignore`）：

| 模式 | 原因 |
|---|---|
| `patches/` | 改动前的文件备份副本，体积大且含运行时数据快照 |
| `**/*.jsonl` | 原始审计/回放调用流水 |
| `probe_results*.json`、`replay_results*.json`、`context_data.json` | 探针与回放原始输出 |
| `**/_*.py`、`**/_*/` | 一次性探针脚本与临时目录 |
| `**/__pycache__/`、`**/*.tmp` | 缓存与临时文件 |

原则：**报告进仓库，原始产物不进**——原始产物体积大、含运行时痕迹，且结论已在报告里固化。

## 为什么值得读

举两个例子：

1. **`decisions/007_model_gateway_migration.md`** 记录了「403 里『这个 key 不能访问某个模型』是按模型的能力限制，不是全局鉴权故障」——若按 401 处理，一次权限配置就会中止整池尝试。
2. **`findings/model_pool/PROBE.md`** 实测了两类 400 的 `error.type` 都是 `invalid_request_error`（差别只在 message），推翻了「参数错 = `litellm.BadRequestError`」的假设，分流判据因此改为 type + message 双判。

「先取证、再动手、留审计、记决策」——这是这份档案真正想传递的东西。
