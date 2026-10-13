# 模型调用超时保护

持续分块返回的 HTTP 响应会不断重置读取超时；旧实现的总预算仅在请求之间检查。
另外，取消 `communicate()` 不会自动终止子进程，旧路径会在超时返回后留下进程。

现在单次尝试和整轮调用都有墙钟时限，仍沿用模型池的 20 秒/45 秒配置。
子进程 60 秒上限包含启动和通信，并预留 2 秒回收；超时或取消后杀死、排空管道并等待退出。
操作系统尚未返回进程句柄时保留一次回收回调，创建完成即终止，不发起重试。
外部取消不会设置故障冷却或触发备用模型。

组合验收补充：Python 3.10 的 `wait_for(shield(task))` 在任务完成与取消同时到达时可能吞掉取消。
进程创建、通信和回收阶段改用不吞取消的截止时间等待，原有 60 秒/2 秒配置不变。
`cleanup_observer` 只在发生回收时通知调用方一次，供 #5 保留仍被实际子进程占用的名额；
本 PR 独立可用，未设置观察者时行为不变。观察者异常也不影响进程回收。
最小复现：`python -m unittest scripts.test_llm_timeouts.ProcessTests.test_cancel_at_creation_or_communication_completion_is_not_swallowed -v`。
修复前取消可能变成正常返回，聊天继续调用备用脚本；修复后始终传播 `CancelledError` 并完成回收。

离线复现与回归（项目虚拟环境，UTF-8）：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m unittest scripts.test_llm_timeouts -v
python -c "import reverse_bot; print('Import OK')"
```

测试仅访问本地 HTTP 桩并启动测试用 Python 子进程，无真实模型或 QQ 请求。
覆盖慢分块、整轮预算、单次失败后降档、外部取消、创建中取消和实际进程回收。
`[模型耗时]` 日志记录整轮耗时、尝试次数和结束原因，原逐次审计字段不变。

文字命令另行回归：`python -m unittest scripts.test_text_command_regression -v`，使用临时签到/菜单数据，覆盖签到归属、吃啥、帮助与管理员权限。
