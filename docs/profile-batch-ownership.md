# 画像批次归属修复

本分支从上游 `main` 的 `d56c917` 独立建立，无前序合并依赖。产品代码只修改
`scripts/user_profile.py`，解决重复消费和重试覆盖新增任务两项问题。

## 两条具体复现

所有发言、队列和画像文件均为隔离测试数据，模型调用使用 `AsyncMock`。

1. 受限时段且 NapCat 已连接时，记录同一用户的 5 条发言。模拟常规更新失败，
   随后重试成功，再执行一次常规更新。基线调用画像生成 **3 次**，第三次重复处理
   已成功的五条发言；修复后只有首轮失败和重试成功的 **2 次**，队列及缓冲均为空。
2. 受限时段且 NapCat 离线时，让旧用户的重试停在事件屏障上。记录新用户的 5 条发言，
   同时触发常规更新，两次模型调用都模拟失败。基线重试结束时整体赋值队列，
   新用户消失，只剩旧用户；修复后两个入口依次领取，队列保留两个用户。

最小复现测试（在本分支根目录使用项目虚拟环境；独立 worktree 可替换解释器路径）：

```powershell
$env:PYTHONIOENCODING='utf-8'
& .\venv\Scripts\python -m unittest scripts.test_profile_batches.ProfileBatchTests.test_retry_success_consumes_batch_before_next_regular_cycle -v
& .\venv\Scripts\python -m unittest scripts.test_profile_batches.ProfileBatchTests.test_retry_and_regular_update_keep_new_pending_user -v
```

基线对照也已实际运行：把 `d56c917` 的模块加载到测试用独立模块对象，两个测试分别因
`3 != 2` 和待处理队列缺少 `new` 用户失败；同一基线上的 8 项策略行为测试全部通过。
需要复核基线时，在本分支根目录执行下面的隔离加载，不必覆盖工作区文件：

```powershell
@'
import subprocess
import types
import unittest
from scripts import test_profile_batches as tests
source = subprocess.check_output(['git', 'show', 'd56c917:scripts/user_profile.py']).decode('utf-8')
baseline = types.ModuleType('profile_baseline')
baseline.__file__ = tests.profile.__file__
exec(compile(source, baseline.__file__, 'exec'), baseline.__dict__)
tests.profile = baseline
names = ['test_retry_success_consumes_batch_before_next_regular_cycle',
         'test_retry_and_regular_update_keep_new_pending_user']
suite = unittest.TestSuite(tests.ProfileBatchTests(name) for name in names)
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(tests.ProfilePolicyTests))
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert len(result.failures) == 2 and not result.errors
assert {case._testMethodName for case, _ in result.failures} == set(names)
'@ | & .\venv\Scripts\python -
```

## 批次如何领取和归还

- 常规更新和重试共用一个 `asyncio.Lock`，取得锁后才领取批次；记录发言不取得此锁，
  等待模型期间仍能追加新发言。两个重试入口同时触发时，已成功消费的批次不会再次领取。
- 待重试队列按群、用户保留有序批次。原先在两个队列同时保存的批次只保存一次，
  用 `regular_fallback` 标记其常规周期兜底资格。常规周期只领取带此标记的旧批次和新缓冲，
  重试入口领取全部待重试批次；同一用户本次领取的数据按先后合并为一次画像调用。
- 只等重试的批次不会因为新发言到来而扩大调度资格。成功只消费已领取数据；
  失败按下表归还，保留其他用户及该用户的其他批次，旧数据放在处理期间新收数据之前。
- 不按文本去重，五次相同的真实发言仍是五条。待处理数量继续按用户数统计，
  多个批次的同一用户只计一次；公开接口和 `user_profiles.json` 的群号→用户号→画像格式不变。

| 入口及失败条件 | 归还位置（沿用原策略） |
| --- | --- |
| 常规更新返回空，受限时段、已连接 | 待重试队列，允许常规周期兜底 |
| 常规更新返回空或异常，受限时段、离线 | 仅等待重试 |
| 常规更新其他失败，包括受限时段已连接时抛异常 | 普通缓冲 |
| 重试返回空或异常，受限时段、离线 | 待重试队列，保留每批原有兜底标记 |
| 重试其他失败 | 普通缓冲 |

常规周期仍为 7200 秒，受限时段仍为 09:00–12:00、14:00–18:00，最少发言数仍为 5。
`reverse_bot.py` 的启动、重连及 5 分钟重试调度未修改。没有新增依赖、轮询或定时任务。
共享锁使画像更新串行，不阻塞事件循环，也不锁住新发言记录。

## 验证结果

```powershell
$env:PYTHONIOENCODING='utf-8'
& .\venv\Scripts\python -m unittest scripts.test_profile_batches -v
& .\venv\Scripts\python -c "import reverse_bot; print('Import OK')"
```

2026-10-13 使用现有 Python 3.10 虚拟环境验证：

- **27 项专项测试通过**：两条复现、常规/重试双向交错、两个重试入口、同一用户新旧批次、
  成功及失败期间追加发言、兜底资格、阈值、失败与异常归还、用户计数、公开接口及文件格式。
- 其中 **8 项策略行为测试**锁定受限时段边界、常量、主备尝试次数、提示词与最近 20 条发言、
  以及备用失败时返回无效首轮画像的现有结果。`_validate_profile` 及之后的模型调用、
  画像回退代码已与基线逐字核对，未修改。
- 本分支 UTF-8 导入自检通过；复用既有的 2 项文字命令回归在本分支通过。
- 在本地组合副本合入前四项优化及互动菜单后，**100 项离线测试通过**，组合导入自检通过。
  此修复在组合副本中无冲突，不引入前序合并依赖。

组合验证所用版本：互动菜单 `c16f600`，超时保护 `1661c78`，非阻塞工作 `9be8ac0`，
统一模型池 `3e33647`，有界消息队列 `4f5944a`。组合命令如下（其他套件由对应分支提供）：

```powershell
& .\venv\Scripts\python -m unittest scripts.test_llm_timeouts scripts.test_async_message_work scripts.test_shared_llm_calls scripts.test_message_queue scripts.test_text_command_regression scripts.test_interaction_menu scripts.test_optimization_integration scripts.test_profile_batches -q
```

本次第五项回复逻辑调整已取消，不修改回复去重或短句校验；画像模型尝试策略、校验和回退行为
全部保留，不纳入取消相关修改。保留原许可声明，排除既有 `w1_regression_output.txt` 换行差异。
测试未访问真实 QQ、模型或画像/签到数据；本次不自动合并或部署。
