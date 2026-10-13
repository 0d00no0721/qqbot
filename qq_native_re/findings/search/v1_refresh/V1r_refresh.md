# V1r — 两处过期测试预期刷新（实现子 agent 交付报告）

- 日期：2026-10-13
- 范围：**只改测试**，未动任何运行时文件
- 改动文件：
  - `qq_native_re/findings/reply_fix/w3_d6_fix_check.py`
  - `qq_native_re/findings/search/v1_backend/_v1_drill.py`
- 备份：`qq_native_re/patches/test_refresh_backup_20261013-072149/`
- 产物目录：`qq_native_re/findings/search/v1_refresh/`

## 0) 结论速览（真跑，exit code 已核对）

| 套件 | 刷新前（基线） | 刷新后 |
|---|---|---|
| `w3_d6_fix_check.py` | **1 项 FAIL**：`用例判定不符: 边界: 过长(600字, max=500) → valid=True reason=None` → exit 1 | **总体 ALL PASS，0 项 FAIL → exit 0** |
| `_v1_drill.py` | 判定表 12 条中 **1 条不符**（第 3 条 `最新的 QQ 版本是多少`）；**硬失败 1 条**（超时用例）→ exit 1 | **判定表 14/14、0 条不符；硬失败 0 条 → exit 0** |

| 反向证明 | 结果 |
|---|---|
| `w3_d6_fix_check.py --sabotage` | **exit 0**（真配置 PASS / 坏配置 FAIL） |
| w3 长度边界双向消融（内存改 max_length） | 5000→10 翻红 1 例；5000→1000000 翻红 1 例 ✓ |
| `_v1_drill.py --sabotage-t1`（内存摘掉 `天气`） | **exit 1**、判定表 1 条不符 + 硬失败点名 ✓ |
| `_v1r_reverse_proof.py`（A/B/C 三段合并证明） | **exit 0**：两次改坏都被抓住 ✓ |

## 1) 为什么是"改测试"而不是"修 bug"

两处预期都是**有意产品变更**后过期的：

1. **max_length 500 → 5000**（**2026-10-12**，用户明确产品要求）：证据
   `qq_native_re/findings/reply_fix/W5_maxlen_5000.md`（改前后 SHA256、47 个叶子键
   逐键比对、`5001 字 → 内容过长 (5001 > 5000)` 实测原文）。现值
   `config/decision_rules.json:23 = 5000`。
   ⇒ w3 套件里写死的"600 字必须判无效"是**过期预期**。
2. **T1 词表收窄**（polish P3，2026-10-13 06:44）：按 **135 条真实群消息回放**
   证据把 `最新/今天/现在/价格/版本` 等宽词移出 `_REALTIME_WORDS`
   （证据 `qq_native_re/findings/search/v1_polish/replay_set.json`、
   `scripts/search_gate.py:63-69` 注释；`V1_search_backend.md:35` 记录宽版
   "1 次命中 / 100% 误报"）。`最新` 已不在 `_REALTIME_WORDS` / `_STRONG_WORDS`。
   ⇒ 演练表第 3 条 `("最新的 QQ 版本是多少", True)` 是**过期预期**（该句仍可由
   T2 路由器兜底，见演练第 4 节）。

## 2) 改了什么

### 2.1 `w3_d6_fix_check.py`（长度边界改为按当前配置动态取值）

- 新增 `_read_max_length()`：**现读** `config/decision_rules.json` 的
  `failover.content_checks.max_length`（读不到才退回引擎快照值 / 500），
  `MIN_LEN` 同样现读；删除写死字数的 `LONG_600`。
- 边界组由 8 条 → 10 条，其中 4 条动态：

  | 用例（名字按现读值生成） | 期望 | 实测（真配置 max=5000） |
  |---|---|---|
  | `边界: 过短(2字, min=3)` | 无效 + 理由含"过短" | `内容过短 (2 < 3)` |
  | `边界: 恰好(3字, min=3) 应有效` | 有效 | valid=True |
  | `边界: 过长(4999字, max=5000-1) 应有效` | 有效 | valid=True |
  | `边界: 过长(5001字, max=5000) 应无效` | 无效 + 理由含"过长" | `内容过长 (5001 > 5000)` |

- 注释就地注明"**max_length 曾于 2026-10-12 由 500 放宽到 5000**"
  （模块 docstring + 用例旁 + 断言旁，共 3 处）。
- 新增 4 条 `V1r 长度边界·…` 断言，且**理由文案**按现读值拼写
  （防止"理由点错一侧"仍被判绿）。
- **未动**：空串/纯空白/换行制表/纯 emoji/乱码/敏感词 6 条边界；
  应拦 24 例、应放行 14 例分组逐条一字未改；熔断 / e2e / 保真度 / 配置健壮性 /
  只读完整性断言全部保留。

### 2.2 `_v1_drill.py`（第 3 条翻转为不触发 + 补 T1 正例）

- 第 3 条 `("最新的 QQ 版本是多少", True)` → **`False`**，旁注收窄原因与证据路径。
- 新增 2 条**确定走 T1 词表**的正例（"实时词 **且** 疑问词"双命中，只含疑问词会
  落到 T2/默认不搜）：`深圳限号吗`（限号+吗）、`今晚球赛的赛程出了吗`（赛程+吗）。
- 表规模 12 → **14 条**；其余 6 项（实弹搜索 / 降级 / T2 / 限流 / fail-closed）
  断言语义一字未动。
- 新增 2 个开关（默认不改变原有行为）：
  - `--strict-table`：判定表出现不符即计入硬失败 → exit 1（回归守门）；
  - `--sabotage-t1`：**内存消融** `_REALTIME_WORDS` 里的 `天气` 并计入硬失败
    （磁盘文件不动），用于反向报警证明。

### 2.3 ⚠️ 偏离说明：顺手修了第 2 节"超时 50ms"的环境 flaky（**非产品语义**）

- 现象（基线真跑）：`超时 50ms → ok=True reason= elapsed=323ms` → `[!!]`，
  是刷新前**唯一的硬失败**，导致 `_v1_drill` 无法 exit 0。
- 根因：该用例重发**与上一用例相同的查询词 `NapCat`**，命中 DSH bridge 缓存
  （`cache=hit`）→ **根本没走到超时路径**；且该断言**自我投毒**（被客户端中断的
  请求在 bridge 侧仍会完成并写缓存）。
- **独立复核**：并行 worker 在 `qq_native_re/findings/search/v1_pages/V1g_pages.md`
  §6 得出同一根因，并给出与我相同的修法建议（唯一查询串），但因其文件权限范围
  无法修改 drill。
- 处置：在**我权限内的 `_v1_drill.py`** 里把该用例改为**唯一查询串**强制
  cache-miss（断言仍为 `ok=False 且 sources 空`，语义未改）。
- 证据见 §4.3；若 PM 不追认，用备份一行回滚即可（代价：该套件退回 exit 1）。

## 3) 复跑证据（真跑原文）

命令：
```powershell
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w3_d6_fix_check.py
& ".\venv\Scripts\python" qq_native_re\findings\search\v1_backend\_v1_drill.py --strict-table
```

### 3.1 `w3_d6_fix_check.py` → **exit 0**
（原文全文：`_v1r_w3_after.txt`）
```
  >>> V1r 长度边界（现读 config: min_length=3 / max_length=5000；max_length 曾于 2026-10-12 由 500 放宽到 5000）
  [PASS] V1r 长度边界·过短: 边界: 过短(2字, min=3)… → valid=False   len=2 reason='内容过短 (2 < 3)'
  [PASS] V1r 长度边界·有效: 边界: 恰好(3字, min=3) 应有效… → valid=True   len=3 reason=''
  [PASS] V1r 长度边界·有效: 边界: 过长(4999字, max=5000-1… → valid=True   len=4999 reason=''
  [PASS] V1r 长度边界·过长: 边界: 过长(5001字, max=5000) … → valid=False   len=5001 reason='内容过长 (5001 > 5000)'
      应拦截组 24 例 → 判无效 24 例  ✓ 全拦
      应放行组 14 例 → 判有效 14 例  ✓ 全放
  [PASS] config/decision_rules.json: 运行前后 SHA256 不变（本套件只读，未改运行时文件）   sha256=dc940d4d3ccd…
  [PASS] scripts/decision_engine.py: 运行前后 SHA256 不变（本套件只读，未改运行时文件）   sha256=c749355e2733…
==============================================================================
总体: ALL PASS
结果已写盘: E:\QQbot\qq_native_re\findings\reply_fix\w3_d6_results.json
```
exit=0；总断言 86 条、0 FAIL。

### 3.2 `_v1_drill.py --strict-table` → **exit 0**
（原文全文：`_v1r_drill_after.txt`）
```
  [OK]  超时：ok=False、sources 空、未抛异常
   [OK] 1  搜索 今天深圳天气      判定=搜    tier=T0   查询=今天深圳天气           | 显式指令
   [OK] 2  今天深圳天气怎么样      判定=搜    tier=T1   查询=今天深圳天气怎么样        | 实时词规则命中（天气+怎么）
   [OK] 3  最新的 QQ 版本是多少   判定=不搜   tier=none 查询=-                | 未命中任何触发条件（默认不搜）
   [OK] 4  苹果股价现在多少       判定=搜    tier=T1   查询=苹果股价现在多少         | 实时词规则命中（股价）
   [OK] 5  深圳限号吗          判定=搜    tier=T1   查询=深圳限号吗            | 实时词规则命中（限号+吗）
   [OK] 6  今晚球赛的赛程出了吗     判定=搜    tier=T1   查询=今晚球赛的赛程出了吗       | 实时词规则命中（赛程+吗）
   [OK] 7  昨晚那场球谁赢了       判定=搜    tier=T1   查询=昨晚那场球谁赢了         | 实时词规则命中（谁赢了）
   [OK] 8  查一下 NapCat 更新内容 判定=搜    tier=T0   查询=NapCat 更新内容      | 显式指令
   [OK] 9–14（6 条负例）          判定=不搜   tier=none
   统计：14 条，0 条与预期不符（与预期不符=产品行为，需人工裁决）
   统计：5 条，0 条与预期不符            ← T2 实弹
判定表：0 条与预期不符
硬失败（降级/限流/fail-closed 契约项）：0 条
```
exit=0。

### 3.3 基线对照（刷新前，为完整起见）
```
1 项 FAIL: 用例判定不符: 边界: 过长(600字, max=500) → valid=True reason=None      ← w3
[!!] 3 最新的 QQ 版本是多少 判定=不搜                                            ← drill 表
[!!] 超时：ok=False、sources 空、未抛异常 → 硬失败 1 条                          ← drill 硬失败
```

## 4) 反向报警证明（"改坏即报警"）

### 4.1 w3 `--sabotage`（关键词维度，原套件自带）→ **exit 0**
（原文：`_v1r_w3_sabotage.txt`）
```
  对比 A｜真配置（22 条）: 契约用例 38 例 → PASS（0 例偏差）
  对比 B｜坏配置（22 条，仅移除 'internal error'）: 契约用例 38 例 → FAIL 4 例
        [FAIL] 拦截｜错误话术: Internal error 前缀 → valid=True reason=None（期望 valid=False）
        [FAIL] 拦截｜错误话术: 小写 internal error → valid=True reason=None
        [FAIL] 拦截｜错误话术: 中文夹杂 → valid=True reason=None
        [FAIL] 拦截｜错误话术: 全大写 INTERNAL ERROR → valid=True reason=None
  [PASS] 消融 ['internal error'] → Internal error: x 必须变为放行（契约随之翻红）
  [PASS] 消融 ['server error'] / ['an error occurred','error occurred'] / ['traceback …'] 同理
反向验证结论: **契约具备真报警能力**（真配置 PASS / 坏配置 FAIL 且点名到具体用例）
exit=0
```

### 4.2 w3 长度维度双向消融（内存改 `max_length`，**不写配置文件**）
```
  [PASS] S-D 对照：真配置（max_length=5000）下长度边界 4 例全部 PASS   偏差=[]
        max_length 5000 → 10（内存）: 翻红 1 例
          [FAIL] 边界: 过长(4999字, max=5000-1) 应有效 → len=4999 valid=False reason='内容过长 (4999 > 10)'（期望 valid=True）
  [PASS] S-D 调小 max_length（5000→10）长度边界必须翻红（证明真会报警）
        max_length 5000 → 1000000（内存）: 翻红 1 例
          [FAIL] 边界: 过长(5001字, max=5000) 应无效 → len=5001 valid=True reason=None（期望 valid=False）
  [PASS] S-E 调大 max_length（5000→1000000）长度边界必须翻红（反方向也会报警）
  [PASS] S 只读完整性: config/decision_rules.json 未被本套件改写   sha256=dc940d4d3ccd…
```
手法：`DE()` 构造真引擎后只替换内存 dict `eng._content_checks["max_length"]`
（`_engine_with_max_length()`），**配置文件全程只读**（SHA256 自证）。

### 4.3 `_v1_drill.py --sabotage-t1`（内存摘掉 T1 词表 `天气`）→ **exit 1（抓住）**
（原文：`_v1r_drill_sabotage.txt`）
```
   [消融] 内存中 _REALTIME_WORDS 去掉 '天气'：15 词 → 14 词（磁盘文件未改）
   [!!] 2  今天深圳天气怎么样      判定=不搜   tier=none 查询=-  | 未命中任何触发条件（默认不搜）
   [OK] 3  最新的 QQ 版本是多少   判定=不搜   …（本就走 T2，不受该消融影响）
   [OK] 5  深圳限号吗          判定=搜    tier=T1  | 实时词规则命中（限号+吗）
   统计：14 条，1 条与预期不符
判定表：1 条与预期不符
硬失败（降级/限流/fail-closed 契约项）：2 条
  [!!] T1 表（内存消融后）判定不符：今天深圳天气怎么样
```
手法：`search_gate._REALTIME_WORDS` 是模块级元组、`_t1_hit()` 在函数内查找它
（`search_gate.py:239`）⇒ 进程内替换模块属性即可生效，`finally` 里还原；
磁盘上的 `scripts/search_gate.py` 与 `config/search.json` **未被修改**。

### 4.4 合并证明 `_v1r_reverse_proof.py` → **exit 0**
（原文：`_v1r_reverse_proof.txt`）A 段复用 w3 的判定函数做双向消融，
B 段复用 drill 的表做 T1 消融，C 段只读自证：
```
  [PASS] A-0 真配置下套件自身 0 项 FAIL（基线）
  [PASS] A-1 max_length 5000→10（内存）长度边界翻红   翻红 1 例: ['边界: 过长(4999字, max=5000-1) 应有效']
  [PASS] A-2 max_length 5000→1000000（内存）长度边界翻红   翻红 1 例: ['边界: 过长(5001字, max=5000) 应无效']
  [PASS] B-0 真词表下判定表 14/14 全对（基线）   不符=[]
  [PASS] B-1 摘掉 '天气' 后判定表翻红   不符 1 条: ['今天深圳天气怎么样']
  [PASS] B-2 '最新的 QQ 版本是多少' 在真/坏词表下都不搜（V1r 新预期与消融无关）
  [PASS] B-3 消融已在 finally 里还原（模块内存态复原）
  [PASS] C config/decision_rules.json 未被改写   sha256=dc940d4d3ccd…
  [PASS] C scripts/search_gate.py 未被改写   sha256=8414e286f4b2…
结论: **两次改坏都被抓住** —— 刷新后的两套件仍具备真报警能力
```

### 4.5 超时用例的独立取证（回答"是不是把断言放宽了"）
`_v1r_timeout_cache_probe.py`（真跑，原文 `_v1r_timeout_probe.txt`）：
```
   A 同查询(drill 写法)   query=NapCat                     timeout=0.05 → ok=False cache=- elapsed=457ms reason=ReadTimeout: 无详情
   B 同查询(再跑一次)      query=NapCat                     timeout=0.05 → ok=False cache=- elapsed=387ms reason=ReadTimeout: 无详情
   C 唯一查询(cache-miss) query=V1r 超时附证 1791847884     timeout=0.05 → ok=False cache=- elapsed=353ms reason=ReadTimeout: 无详情
   [PASS] 唯一查询 + 50ms 超时 → ok=False（超时语义正常）
```
说明：bridge 缓存**有时效**，故该断言是**间歇性** flaky（基线跑命中 `cache=hit`
→ `ok=True` 失败；本探针跑时缓存已过期 → `ok=False` 通过）。改法的价值正是把
该断言从"取决于缓存是否恰好命中"变成"只取决于超时机制"。
（曾尝试用裸 `httpx` 直连 bridge 取更底层的 cache 字段，但该端点对无鉴权请求返回
**401**，故该路径作废、脚本已删；证据以 `web_search` 层探针为准。）

## 5) 未动运行时文件声明

- 本任务**只改**上述两个测试文件；产物只写在
  `qq_native_re/findings/search/v1_refresh/`（报告 + 日志 + 3 个证明脚本）与
  `qq_native_re/patches/test_refresh_backup_20261013-072149/`（备份）。
- 未碰：`scripts/decision_engine.py`、`scripts/model_primary.py`、
  `scripts/model_fallback.py`、`scripts/search_gate.py`、`scripts/web_search.py`、
  `reverse_bot.py`、`config/decision_rules.json`、`config/search.json`、`.env`、
  NapCat；未重启任何进程。
  （`scripts/web_search.py` + `config/search.json` 是**另一个并行 worker** 的在改
  文件，我一律只读。）
- 运行前后 SHA256 自证（三处独立核对，值一致）：
  - `config/decision_rules.json` = `dc940d4d3ccd…`（= W5 报告记录改后哈希前缀）
  - `scripts/decision_engine.py` = `c749355e2733…`
  - `scripts/search_gate.py` = `8414e286f4b2…`
  - `scripts/web_search.py` = `756762dde6a4…`、`config/search.json` = `51b5bb649223…`
    （探针脚本前后各测一次）

## 6) 产物清单

| 文件 | 说明 |
|---|---|
| `V1r_refresh.md` | 本报告 |
| `_baseline_v1_drill.txt` | 刷新前 drill 基线原文（含 1 条硬失败） |
| `_v1r_w3_after.txt` / `_v1r_w3_sabotage.txt` | w3 刷新后 / `--sabotage` 原文 |
| `_v1r_drill_after.txt` / `_v1r_drill_sabotage.txt` | drill 刷新后（`--strict-table`）/ `--sabotage-t1` 原文 |
| `_v1r_reverse_proof.py` / `.txt` | 反向证明合并脚本与原文 |
| `_v1r_timeout_cache_probe.py` / `_v1r_timeout_probe.txt` | 超时用例 flaky 取证 |
