# W5 — failover.content_checks.max_length 500 → 5000

状态：**已完成**（改动已落盘并复验通过）

## 1) diff 证据（唯一一处改动，+1 字节）

```
DIFF line 23:
  -       "max_length": 500,
  +       "max_length": 5000,
```

- 文件大小：1535 B → **1536 B**；行数：46 → 46（未增删行）
- SHA256 改前 `5315CD05794F43A255958D9F0A8A135AB2CA8CC1289B4ED4A85FB3D2B51F56ED`
- SHA256 改后 `DC940D4D3CCD9EC0D512F177B3F7513519ACCF24F91B6C9A09FFA98266644677`
- 备份目录：`qq_native_re/patches/maxlen_backup_20261012-180437/`（副本哈希与改前原件一致）
- 未删键（保留 `max_length` 键本身）：代码是 `checks.get("max_length", 500)`（`decision_engine.py:796`），删键会退回 500。

## 2) 新建 DecisionEngine 实例复验（真跑，原文见下）

```
实例快照 self._content_checks['max_length'] = 5000
    600 字 -> is_valid=True  reason=None
    900 字 -> is_valid=True  reason=None
   2000 字 -> is_valid=True  reason=None
   4999 字 -> is_valid=True  reason=None
   5001 字 -> is_valid=False reason='内容过长 (5001 > 5000)'
总结论: PASS — 检查仍在，阈值已放宽到 5000
```

边界文案精确匹配 `内容过长 (5001 > 5000)` —— 检查逻辑（`_validate_content:809-810`）**未被删除**，只是 5000 以下不再触发。

## 3) 其它键未动比对

递归压平 47 个叶子键逐键比对（改前备份 vs 改后落盘）：

```
改前叶子键数 = 47 | 改后叶子键数 = 47
新增键 : 无
删除键 : 无
值变化 : 1 处
   failover.content_checks.max_length: 500 -> 5000
比对结论: PASS — 仅目标键变化
```

- `min_length` = 3 ✔（原样）
- `reply_max_tokens` = 500 ✔（原样）
- `error_keywords` = 22 条 ✔（原样）
- `failover.fallback` 整块逐字原样 ✔

## 4) ⚠️ 需重启 bot 才生效

运行中的实例（PID 20428，`:8080` LISTENING，启动于 12:57:29）**仍是 500**：`content_checks` 在 `__init__` 被快照进 `self._content_checks`（`decision_engine.py:218`），**不热加载**。配置 mtime 18:06:00 晚于进程启动时间，故旧实例读到的仍是旧值。**重启 bot 后新值 5000 才生效（重启由用户执行，本任务未重启任何进程）。**

## 5) 未动其它文件的声明

近 60 分钟内被修改的文件仅 3 个：`config/decision_rules.json`（目标）与本次新增的 `qq_native_re/findings/reply_fix/{W5_maxlen_5000.md,_w5_validate.py}`。`.py` 源码（`decision_engine.py` 11:58、`llm_pool.py` 11:08、`reverse_bot.py` 12:35）、`config/model_pool.json`、`.env`、NapCat 目录均未触碰。

### 附注（非本任务范围，仅记录）

验证脚本导入 `decision_engine` 时其日志模块打印 `[警告] 无法写入文件日志 logs\bot.2026-10-12.log（权限不足）`。这是既有的日志权限现象（我的子进程无 `logs\` 写权限），不影响配置改动或校验结论；bot 自身写日志的权限情况未在本任务内变更。
