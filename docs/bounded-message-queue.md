# 消息积压保护

合并依赖：GitHub #2「超时保护」（包含取消交接修复与回收通知）、#3「避免阻塞消息」。
已解除对 #4「减少模型重复请求」的依赖，#4 已撤回。本分支仍从上游 main 独立建立，不夹带依赖的实现；
请先合并 #2、#3，再更新本分支基线和解决接线冲突，完整功能须在组合版本验收。

聊天保留 `model_primary.py` / `model_fallback.py` 两个独立脚本、独立进程和各自的网络状态/模型冷却。
父进程只在文本主备链外申请一次模型名额，主脚本结束才运行备用脚本；识图单独申请并释放名额，
随后文本生成再申请，不在整个聊天流程外占名额。命令与识图的进程内模型池也使用同一父进程上限。
不改提示词、模型顺序、主备策略和作者的回复硬拦截/短句校验/整群精确去重。
进程延迟创建或回收未结束时仍占父进程名额，停止追加备用脚本；正常超时并完成回收后按原策略降档。

旧接收端对每条消息直接创建任务。同群一次发送 20 条明确提问、阻塞第一条生成时，其余 19 条仍积压在业务锁后。
离线复现：`python -m unittest scripts.test_message_queue.QueueTests.test_twenty_explicit_requests_admit_one_active_and_three_waiting -v`。
现在默认接纳 1 条运行项和 3 条明确提问等待项，其余请求受提示冷却限制，返回繁忙提示。

`config/message_limits.json` 按修改时间读取。`defaults` 为公共值，`groups` 用群号覆盖；
全局的 `max_message_tasks`、`max_model_calls`、`max_pending`、`max_prefetch_bytes` 只能放在 defaults。
例如 `"groups": {"123456": {"max_pending_per_group": 2, "notices_enabled": false}}`。
损坏配置沿用上次有效值。调小设置只影响后续准入，已排队请求保留自己的设置与截止时间。

默认入口处理并发 16、模型调用并发 4；每群聊天同时 1 条、明确提问等待 3 条、全局聊天等待 50 条。
闲聊每群只保留最新一条候选；明确提问优先，群间轮转。等待期限明确提问 180 秒、闲聊 15 秒。
这些新增值都是草稿阶段的保守暂定值，需要部署后用真实数据校准。

文字命令在独立的有界入口执行，不占聊天队列。语音转写较慢时，后续聊天按接收顺序写入上下文和发言缓存，文字指令仍可执行。
消息、发送者、图片候选和回应目标入队时固定；闲聊合并及等待过期不会再次记录发言。
去重缓存继续公开导出原 set，满时逐条移除旧 ID，不整体清空。

图片候选元数据在接收阶段固定。普通入口处理和发言记录完成后，先判断聊天准入；
通过消息入口但聊天队列已满的消息，仍按原规则记录一次上下文、画像发言和群历史，只是不下载或生成。
已准入的 @bot/别名识图请求，先执行原有群额度、贴图过滤（引用例外）和同图去重，再立即异步下载；
下载不等同群上条聊天生成完成，出队只用已缓存的字节，不再次访问临时直链、不重复扣额度/查重。
普通未触发识图的图片仍只缓存元数据。每次最多预取原来的 3 个候选，只生成一条成功描述；
相比原来成功即停止下载，可能多预取候选，下载并发、流量和缓存均有上限。

`max_prefetch_bytes` 默认 **33554432（全局 32 MiB）**，包含所有群已下载原图及正在下载的数据。
开始每次下载前保守预留 **2 × 8 MiB**，同时覆盖原始下载缓冲和转成 bytes 的副本；完成后改记实际原图长度。
因此尚有不足一份预留的空间时，也会直接降级文本。此值是附件原始字节预算，不是整个 Python 进程的 RSS 上限；
Pillow 解码/缩放等工作内存另由 #3 的受限执行器控制。
沿用 #3 的最多 2 个并行下载、10 秒超时、8 MiB/图流式上限；不使用同步下载降级，不新增网络重试。
附件只属于当前请求，不写入 image_cache、画像、历史、日志或上下文。完成、合并、过期、断线和关闭时释放，
下载退出前或处理线程实际结束前仍保留其容量，不因协程超时/取消提前退额。空闲不轮询、不定时清理。

排队与模型生成分别计时，模型名额等待发生在模型超时保护之外。
开始运行的聊天不再被后来的候选替换；开始生成后不继承旧的队列截止时间。
断线取消该连接入口及聊天任务，取消等待或名额交接均释放容量。

明确提问等待 10 秒提示一次「已收到，正在排队，请不用重复发送。」；只有容量满或等待过期时提示重试。
提示开关、三种文案、延迟、同群同用户冷却（默认 30 秒）均可配置。
新增日志仅记录排队、预处理、模型及总耗时、尝试次数和结束原因，不追加消息正文或图片数据。
`attempts` 统计本进程 HTTP 尝试，`script_attempts` 统计主备脚本调用；子进程真实 HTTP 尝试保留在原有模型审计中，不把脚本数当作 HTTP 请求数。
调度由到达、完成与有等待项时的一次性到期回调驱动，空闲无轮询、无周期清理。

离线验证（已激活项目虚拟环境）：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m unittest scripts.test_message_queue scripts.test_queue_resources scripts.test_text_command_regression -v
python -c "import reverse_bot; print('Import OK')"
```

覆盖突发 20 条、跨群轮转、闲聊合并、慢回答超过原排队截止时间、满队列/过期、提示去重、配置变更、
取消交接、断线释放、接收顺序、固定图片候选、去重逐条淘汰、文字命令和空闲行为。
不访问真实模型、QQ 或真实签到记录。真实群负载、网关延迟和默认容量仍待部署验收，本次不自动收紧时限。

最小复现与修复后的行为：

- 同群前一条聊天被事件屏障阻塞，紧接一条识图提问。旧版 #5 只保留 URL，出队才下载；
  新版在上一条未完成时已完成下载，模拟 URL 随后返回 403，仍从缓存字节识图且不补下载。
  `python -m unittest scripts.test_queue_resources.IntakeTests.test_queued_image_downloads_immediately_rejected_chat_still_records_once scripts.test_queue_resources.DependencyTests.test_cached_bytes_survive_url_expiry_without_redownload -v`
- 全局模型名额设为 1，依次到达识图、文字命令和文本主备生成；各阶段轮流执行，无嵌套占用/相互等待。
  `python -m unittest scripts.test_queue_resources.DependencyTests.test_single_slot_vision_then_text_and_command_never_nest_or_overlap -v`
- 本地 HTTP 桩对 9 个候选模型均返回作者硬拦截话术。实际运行原始脚本 JSON 入口，观察到两个不同 PID、
  主备各请求一次首模型（合计 2 次），返回空回复。不会套用已撤回 #4 的同轮遍历 9 次策略。
  `python -m unittest scripts.test_queue_resources.DependencyTests.test_primary_fallback_use_distinct_pids_and_unchanged_json_interface -v`
- 多群下载同时被屏障阻塞，预算在下载完成前即占满；后续请求不再下载。关闭/过期/合并释放，
  线程取消但仍运行时保持占额。`python -m unittest scripts.test_queue_resources.PrefetchTests -v`

独立分支的依赖组合测试会明确跳过，须在叠加 #2、#3 后全量执行，不把跳过算作通过。
额外组合验收包含 #1 互动菜单和 #6 画像批次修复，排除 #4；手机/电脑 QQ 和真实网关负载仍待人工验收。

本轮结果（Python 3.10，项目 venv、UTF-8）：独立分支 26 项通过、6 项明确跳过依赖组合场景；
#1 + #2 + #3 + #5 + #6 组合 **108 项通过，0 跳过**，两处导入自检通过。
组合时保留模型池外层准入、内层生成超时，并同时保留菜单回执路由、队列断线清理与异步执行器关闭；
菜单原测试中无群号/发送者的模拟消息补齐这些字段，以符合 #5 的入口校验，未改菜单业务逻辑。
组合测试命令：

```powershell
python -m unittest scripts.test_message_queue scripts.test_queue_resources scripts.test_llm_timeouts scripts.test_async_message_work scripts.test_text_command_regression scripts.test_interaction_menu scripts.test_profile_batches -v
```

额外核对上游 main：内容校验、前置过滤、明确提问去重、主备链函数体 AST 相同；
两个独立模型脚本、模型配置和决策规则未改。离线耗时不作为真实网关性能数据。
