# V1g —— 搜索抓网页正文（fetch_page + enrich_sources）

- 日期：2026-10-13　执行：实现子 agent（h-4 `search-pages`）
- 状态：**完成**（六项验收全绿；§8 记录了两处并发写入者，非本次改动）
- 备份：`qq_native_re/patches/search_pages_backup_20261013-071856/`（web_search.py 10,451 B + search.json）
- 前置守卫：`scripts/web_search.py` mtime 06:42:43（age 2100.8s）、`scripts/search_gate.py` mtime 06:43:17（age 2066.4s）→ **均 >180s，未与 polish 任务撞车**
- 取证脚本（可复跑）：`_v1g_check.py` + `_v1g_check.log`（六项证据）、`_v1g_hot.py` + `_v1g_hot.log`（热加载 transcript）、
  `_v1g_evidence.json`（机器可读）、`_v1_drill_rev14470.log`（drill 复跑原文摘录）

## 0. 验收速览

| # | 判据 | 结果 | 证据 |
|---|---|---|---|
| ① | 至少 1 条 snippet 命中 `\d+\s*[℃°度]` | ✅ **3/3 条**（live 配置 3000）；任务书默认 1200 时 **1/3**（仍达标） | §2 |
| ② | SSRF 拒绝且未发请求 | ✅ 7/7 例 ok=False、elapsed 0–2ms | §3 |
| ③ | 反爬 403 降级 | ✅ ok=False + snippet 逐字保留 | §4 |
| ④ | 并行后总增时 ≤3s | ✅ live 均值 **+0.58s**（首轮含冷连接 +1.46s；最坏=单页超时 8s） | §5 |
| ⑤ | `_v1_drill.py` 复跑 exit 0 | ✅ **EXIT=0、硬失败 0 条**（当前修订版 sha `F2B0E46E…`，07:41:38 复跑）；早期旧修订版的 `EXIT=1` 经 A/B + 机制实测 + PM 独立复现确认是 drill 测试缺陷（固定查询词命中插件缓存），非本次改动 | §6 |
| ⑥ | config 三键热加载 | ✅ fetch_pages 3→0→3 同一 PID，退回纯 snippet | §7 |

## 1. 改动位置

**`scripts/web_search.py`**（254 → 640 行，备份见上）：

- 新增配置读取：`_cfg_int/_cfg_float/_fetch_pages/_page_max_chars/_page_timeout/_page_total_timeout`
  （`_config()` 原样复用，mtime 热加载）；常量 `DEFAULT_FETCH_PAGES=3`、`DEFAULT_PAGE_MAX_CHARS=1200`、
  `DEFAULT_PAGE_TIMEOUT=8.0`、`DEFAULT_PAGE_TOTAL_TIMEOUT=10.0`、`DNS_TIMEOUT=3.0`、`BROWSER_UA`。
- `_decode_entities()`：从 `_clean_text()` 里抽出的公共实体解码（行为不变，纯重构）。
- 正文提取：`_decode_html()`（只在页面自报 gb2312/gbk 时才换 gb18030 解码）、
  `_extract_text()`（去 script/style/noscript/svg/template/iframe → 去注释 → 去标签 → 解实体 → 折空白 → 截断）。
  **顺序上有一处刻意偏离任务书**：任务书写"折空白 → 解实体"，实现改成"解实体 → 折空白"，否则 `&nbsp;`
  会以裸 `\xa0` 留在正文里；去标签仍放在解实体之前，避免 `&lt;script&gt;` 被误删。
- URL 守卫：`_ip_verdict()`、`_resolve_ips()`（事件循环内 DNS，3s 上限，IP 字面量不走 DNS）、`_url_guard()`。
- **`async def fetch_page(url, *, max_chars=None, timeout=None) -> dict`** → `{"ok","text","chars","reason","elapsed_ms"}`，
  永不抛异常；httpx `trust_env=False` + 浏览器 UA + `follow_redirects=True`；非 200（401/403/429 标注"疑似反爬拦截"）、
  非文本 content-type、正文为空、任何异常 → 全部收敛成 `ok=False` + 中文 reason。
- **`async def enrich_sources(sources, *, limit=None, max_chars=None, timeout=None) -> list`**：前 `fetch_pages` 条
  `asyncio.gather` 并行 + 共享连接池；成功 → `snippet` 换成正文、`page_ok=True`、`page_chars=N`；失败/空 → 原样保留
  snippet、`page_ok=False`；整体 `asyncio.wait_for` 总闸（默认 10s，`max(10, page_timeout+2)`），超时用**已完成**的结果；
  `limit<=0` → 原样返回、不新增键；返回浅拷贝，不改调用方对象。
- `search()` 新增关键字参数 `with_pages: bool = True`（位置参数与返回既有键一字未改）；返回新增
  `page_elapsed_ms`/`pages_fetched`，`_fail()` 也补上这两个键（成功/失败形状一致）。
- docstring 写明：**`snippet` = 给模型看的最佳可用文本（抓到的正文优先，否则搜索摘要）**，`raw_snippet` 永远保存原始摘要。

**`config/search.json`**：只**新增**三键 —— `"fetch_pages": 3`、`"page_max_chars": 1200`、`"page_timeout_seconds": 8`；
现有 10 个键（enabled/tiers/rate_limit_per_hour/max_results/timeout_seconds/router_max_tokens/router_timeout_seconds/interim_notice/base_url）一字未动。

## 2. 温度命中（验收 ①）

### 2.1 主证据（live 配置 `page_max_chars=3000`，`_v1g_check.log`，EXIT=0）

`await search("今天深圳天气", 3)` → `ok=True provider=bing`，bridge 838ms、抓正文 **604ms**、正文 **3/3 条**：

| # | page_ok | page_chars | 温度命中 | snippet 前 120 字 |
|---|---|---|---|---|
| 1 `tianqi.com/shenzhen/today/` | **True** | 2680 | **`['25 ℃','33℃','14℃','14℃','38℃','38℃']`** | `【深圳今天天气预报】深圳天气预报24小时详情_深圳天气网 天气网提供全国国内城市天气预报，旅游景点天气预报，国际城市天气预报以及历史天气预报查询 旅游景点 \| 美食 \| 特产 \| TAG标签 \| 便民信息 \| 天气 天气预警 天气插件 微信` |
| 2 `weather.com.cn/weather/101280601.shtml` | **True** | 3000 | **`['24℃','25℃','24℃','25℃','25℃','25℃']`** | `深圳天气预报,深圳7天天气预报,深圳15天天气预报,深圳天气查询 首页 预报 预警 雷达 云图 天气地图 专业产品 资讯 视频 节气 更多 台风路径 空间天气 图片 专题 环境 旅游 碳中和 气象科普 一带一路 产创平台 国内 本地 国际 …` |
| 3 `weather.com.cn/weather40dn/101280601.shtml` | **True** | 2737 | **`['25 °','25 °','25 °','24 °','24 °','24 °']`** | `【深圳天气】深圳今天天气预报,今天,今天天气,7天,15天天气预报,天气预报一周,天气预报15天查询 首页 预报 预警 雷达 云图 天气地图 专业产品 资讯 视频 节气 更多 台风路径 空间天气 图片 专题 环境 旅游 碳中和 气象科普 一` |

**>>> 命中 `\d+\s*[℃°度]` 的条数 = 3/3（期望 ≥1）✅**

### 2.2 对照：按任务书默认值 `page_max_chars=1200` 的首轮同测（改造刚完成时）

`ok=True provider=bing`，bridge 866ms、抓正文 8712ms（含 1 条 8s 超时）、正文 2/3 条：

| # | page_ok | page_chars | 温度命中 | snippet 前 120 字 |
|---|---|---|---|---|
| 1 `tianqi.com/shenzhen/today/` | False | 0 | — | `2024年9月24日 · 深圳天气网为您提供深圳天气预报24小时详情、深圳今日天气预报，包括今日实时温度、24小时降水概率、湿度 … 阅读更多`（原搜索摘要，ReadTimeout 降级） |
| 2 `weather.com.cn/weather/101280601.shtml` | **True** | 1200 | **`['24℃','25℃','24℃','25℃','25℃','25℃']`** | `深圳天气预报,深圳7天天气预报,深圳15天天气预报,深圳天气查询 首页 预报 预警 雷达 云图 天气地图 专业产品 资讯 视频 节气 更多 台风路径 空间天气 图片 专题 环境 旅游 碳中和 气象科普 一带一路 产创平台 国内 本地 国际 …` |
| 3 `weather.com.cn/weather40dn/101280601.shtml` | True | 1200 | — | `【深圳天气】深圳今天天气预报,今天,今天天气,7天,15天天气预报,天气预报一周,天气预报15天查询 首页 预报 预警 …`（前 1200 字是导航样板，温度在其后） |

**>>> 该轮命中 `\d+\s*[℃°度]` 的条数 = 1/3（仍满足 ≥1，但第 3 条因截断丢了数据）**

温度在正文里的确切位置（`fetch_page(URL, max_chars=0)`，全页 4326 字，耗时 611ms）：
`24℃@484、25℃@503、24℃@523、25℃@543、25℃@563、25℃@583、25℃@603 …20℃@2020`
→ 第 2 条在 1200 字内仍含温度；但第 3 条（weather40dn）的温度在 **1386 字之后** ⇒ 1200 上限会漏 → 见下。

`raw_snippet` 未被覆盖（逐条核对，例）：第 2 条 `23 小时之前&ensp;·&ensp;深圳天气预报，及时准确发布中央气象台天气信息…`。

已知局限（**已由 PM 终验实测坐实并调参**）：正文开头 ~200 字是站点导航样板；第 3 条 1200 字内没等到温度。
PM 在 `PM_ACCEPTANCE.md` §二 记录："实测温度出现在正文第 **307/484/1386** 字处，1200 字截断会漏掉后文数据
⇒ 判据1 一度失败（0/3）；改为 3000 后稳定 **3/3**" → **live `page_max_chars` 已由 PM 改为 3000**（07:24:24，
本实现 mtime 热加载，无需改代码、无需重启；我按任务书写的默认值仍是 1200，仅当配置键缺失时才会回退）。
PM 终验结论（判据1）：`[搜索] 抓正文 3/3 条成功 耗时=568ms`，正文含 `25 ℃ 晴 25 ~ 33℃`、`13日（今天） 晴 33 / 24℃`、
`佛山 34 / 25 °C` —— 这是"资料块能包含真实数据"的端到端实证。

> **建议留给 DM 决定**：若希望配置键缺失时也不掉到 1200，把 `DEFAULT_PAGE_MAX_CHARS` 同步改成 3000 即可（一行）；
> 我未擅自改，因为任务书明确写"默认 1200"，且 live 值已由 PM 定在 3000。

## 3. SSRF 三例（验收 ②，另加 4 例）

| URL | ok | elapsed_ms | reason |
|---|---|---|---|
| `http://127.0.0.1:19387/api/dsh-free-search-settings/describe`（PM 实证能拿到本机响应） | False | **0** | `拒绝内网地址` |
| `http://192.168.1.1/`（改造前会 ConnectTimeout 12.2s） | False | **0** | `拒绝内网地址` |
| `file:///C:/Windows/win.ini` | False | **0** | `拒绝非 http(s) 协议（file）` |
| `http://localhost:19387/…/raw-search` | False | 2 | `拒绝内网地址` |
| `http://[::1]:19387/` | False | 0 | `拒绝内网地址` |
| `http://169.254.169.254/latest/meta-data/`（云元数据） | False | 0 | `拒绝内网地址` |
| `http://10.0.0.1/` | False | 0 | `拒绝内网地址` |

**>>> 全部 `ok=False`，且 `elapsed_ms<50ms`（= 判定全在发请求之前完成）= True ✅**
判定链：仅 http/https → 主机名（IP 字面量免 DNS；域名 `loop.getaddrinfo` 3s 上限）→ 每个解析结果过
loopback/private/link-local/reserved/multicast/unspecified + 显式补充网段（0.0.0.0/8、100.64/10、192.0.2/24、
198.18/15、240/4、fc00::/7、fe80::/10、::/128…），任一命中即拒；`::ffff:127.0.0.1` 按 IPv4 语义判；
DNS 失败/超时同样拒绝（fail-closed）。

## 4. 反爬降级（验收 ③）

```
fetch_page(https://www.zhihu.com/question/19550224) → ok=False reason=HTTP 403（疑似反爬拦截） elapsed=585ms
enrich_sources([知乎条目, napneko.github.io], limit=2) →
  [1] page_ok=False page_chars=0 snippet=原始搜索摘要-知乎条目      raw_snippet=原始搜索摘要-知乎条目
  [2] page_ok=True  page_chars=416 snippet=NapCatQQ | 现代化的基于 NTQQ 的 Bot 协议端实现 Skip to content …  raw_snippet=原始搜索摘要-文档条目
>>> 403 条：不抛异常、snippet 逐字保留原摘要=True；好页：snippet 被正文替换=True ✅
```

## 5. 耗时对比（验收 ④）

同一查询连打（bridge 走缓存，`with_pages=False` 的 ~300ms 即纯 bridge 往返，差值=纯抓正文开销）。

**(a) live 配置（`page_max_chars=3000`，`_v1g_check.log` 第二轮）**

| 采样 | with_pages=False | with_pages=True | 差值 |
|---|---|---|---|
| 1 | 377ms | 842ms（page 483ms） | +465ms |
| 2 | 270ms | 963ms（page 470ms） | +693ms |
| 3 | 275ms | 858ms（page 414ms） | +583ms |
| **均值** | **307ms** | **888ms** | **+580ms ✅ ≤3s** |

**(b) 首轮（`page_max_chars=1200`，含冷连接 TLS 的悲观样本）**

| 采样 | with_pages=False | with_pages=True | 差值 |
|---|---|---|---|
| 1（冷连接 TLS） | 311ms | 3864ms（page 3561ms） | +3553ms |
| 2 | 317ms | 714ms（page 430ms） | +397ms |
| 3 | 301ms | 743ms（page 374ms） | +442ms |
| **均值** | **310ms** | **1774ms** | **+1464ms ✅ ≤3s** |

并行有效性：3 页整批 414–604ms（≈单页耗时，不是 3 倍）；`page_elapsed_ms` 与 `pages_fetched=3` 均如实回报。
最坏情况有界：首轮里 tianqi.com 曾挂到单页超时（8s）→ 整批 8712ms，第二轮同一站点 2680 字仅 604ms（站点抖动）；
总闸 10s 兜底，超时只影响未完成条目（其余用已完成结果，不回吐异常）。
**建议**：若想进一步压缩回复延迟，把 `page_timeout_seconds` 调到 4、`fetch_pages` 调到 2（均为热加载，无需重启）。

## 6. 回归 `_v1_drill.py`（验收 ⑤）

**结论：在当前修订版上 `EXIT=0、硬失败 0 条` ✅；此前那一轮的 `EXIT=1` 是 drill 自身的测试缺陷（固定查询词命中插件缓存），与抓正文改动无关。**

0. **当前修订版复跑（验收通过）**：`2026-10-13 07:41:38` 运行
   `_v1_drill.py`（14,470 B，mtime 07:31:16，sha256 `F2B0E46E…DB95`，运行前后哈希一致）→
   **`判定表：0 条与预期不符`、`硬失败：0 条`、`EXIT=0`**（逐字摘录见 `_v1_drill_rev14470.log`）：
   实弹搜索 `抓正文 3/3 条成功 耗时=2260ms`（snippet 已是真实正文）；死端口 `ok=False reason=ConnectError`；
   超时 `50ms → query='NapCat 超时用例 848508062' ok=False reason=ReadTimeout elapsed=559ms`；
   判定表 14/14、T2 5/5 + 端到端、限流 10+1、fail-closed 5/5。
   **该修订版把「超时 50ms」用例改成了唯一查询串**（强制 cache-miss），正是下面第 3 点诊断出的根因修法。
1. **早期一轮（07:26，旧修订版）**：`EXIT=1`，硬失败 1 条 —— 第 2 项 `超时 50ms → ok=False`
   实际得到 `ok=True elapsed=286ms`；**其余 5 项全 `[OK]`**（实弹搜索、T0/T1 判定表 12 条、T2 实弹 5/5、
   限流 10+1、fail-closed 5/5；死端口项 `ok=False reason=ConnectError elapsed=2334ms` 正常）。
2. **A/B 对照**（同一时刻、同一环境，把备份里的改造前模块 load 进来跑同一调用）：
   `改造前(备份) → ok=False reason=ReadTimeout elapsed=482ms`；`改造后(当前) → ok=False reason=ReadTimeout elapsed=370ms`
   → 两个版本**行为一致**，都满足 drill 的断言；说明不是本次改动引入的。
3. **机制实测**（钉死 flaky 根因）：旧断言用的查询词固定为 `"NapCat"`，而 DSH 插件自带缓存：
   `已缓存 NapCat timeout=50ms → HTTP 200，往返 20ms / 6ms`（<50ms ⇒ 不超时 ⇒ `ok=True`）；
   `全新查询 timeout=50ms → ReadTimeout，55ms`（⇒ `ok=False`，断言期望的路径）。
   干扰项：`httpx.AsyncClient()` 构造本身要 ~390ms，所以缓存命中时 `elapsed_ms≈286ms`，
   看着像"超时没生效"，其实是"构造 390ms + 请求 6ms"。
   该断言还是**自我投毒**的：被客户端中断的请求在插件侧仍会完成并写缓存，所以第一次跑通后复跑更容易失败。
4. **PM 独立复现同一结论**（`PM_ACCEPTANCE.md` §三 遗留）："`_v1_drill.py` 的超时用例复用了同一查询 →
   命中 bridge 缓存而绕过超时路径（**测试缺陷**，非产品缺陷）；已指示改为唯一查询串。" → 已由 `v1_refresh` 落地。
5. **该文件在本次会话中被第三方重写两次**（07:29:37 → 07:31:16，9,307 B → 13,245 B → 14,470 B，
   新增 `SABOTAGE_DROP` 内存消融段 + 唯一查询串修 flaky）；我未修改 drill（不在允许改动的文件范围内）。

## 7. 热加载（验收 ⑥）

真实 `config/search.json`、**同一个 python 进程 PID=36884、全程未重启**（transcript 逐字见 `_v1g_hot.log`）：

```
起始 fetch_pages=3
[07:23:49] 热加载生效：fetch_pages 3 → 0（进程未重启，cfg mtime 变了就重读）
[搜索] 结果=3 条 provider=bing 耗时=414ms     [搜索] 正文=0 条 抓取耗时=0ms
   fetch_pages=0 → ok=True page_elapsed_ms=0 pages_fetched=0
   source 键=['raw_snippet','raw_title','snippet','title','url']   无 page_ok 键（=纯 snippet）=True
[07:24:13] 热加载生效：fetch_pages 0 → 3
已看到 3 → 0 → 3 全程，退出。 [exit code 0]
```
→ `fetch_pages=0` 时 `page_elapsed_ms=0`、`pages_fetched=0`、snippet 回到原搜索摘要（逐条与 raw 摘要一致），**行为退回纯 snippet ✅**
API 级同向验证：`enrich_sources(items, limit=0)` → 原样返回、无 `page_ok` 键；`search(..., with_pages=False)` → `page_elapsed_ms=0`、无 `page_ok` 键。

**沙箱限制（需 DM 知晓）**：本会话的**子进程不能写 `E:\QQbot\config\` 与 `E:\QQbot\scripts\`**
（`PermissionError [Errno 13]`；ACL 与其它目录一致、`config` 目录 Authenticated Users 有 Modify，
`scripts/_probe.json`、`config/_probe.json` 均被拒，而仓库根与 `qq_native_re/` 可写）→ 纯 DSH 沙箱策略。
因此上面的"改配置"由 harness 编辑工具执行（它可写），Python 侧只做只读轮询。
**harness 写 `scripts/web_search.py` 与 `config/search.json` 都是成功的**，落盘不受影响。

## 8. 未动其它文件声明 + 并发写入者

**我的写入范围（mtime 证据）**：本会话 07:17 开始，我只写过两个文件 ——
`scripts/web_search.py`（07:21:11，28,943 B）与 `config/search.json`（三键；末次 07:24:13）。
备份在 `qq_native_re/patches/search_pages_backup_20261013-071856/`。

未改：`decision_engine.py`、`model_primary.py`、`model_fallback.py`、`reverse_bot.py`、`llm_pool.py`、
`scripts/search_gate.py`、`config/group_features.json`、`_v1_drill.py`、`.env`；未重启任何进程、未碰 NapCat。

**并发写入者（同一时间窗内有其它任务在写，特此声明以免误判归属）**：

| 文件 | 时间 | 谁 | 说明 |
|---|---|---|---|
| `config/search.json` → `page_max_chars: 1200 → 3000` | 07:24:24 | **PM** | PM 终验实测温度在第 307/484/1386 字 ⇒ 1200 会漏（`PM_ACCEPTANCE.md` §二）；我按任务书写默认 1200，未回改 PM 的 3000 |
| `_v1_drill.py` 重写（+消融段、+唯一查询串修 flaky） | 07:29:37 → 07:31:16 | `v1_refresh` 任务 | 我未改 drill；验收 ⑤ 应在该修订版上判定 |
| `reverse_bot.py` / `model_primary.py` / `model_fallback.py` / `config/group_features.json` | 07:17:13–07:18:21 | 第 2 波接线任务 | 均早于我对 `web_search.py` 的首次写入（07:21:11），与本次改动无交集 |

`decision_engine.py:940` 的调用是 `web_search.search(query, max_results, None)`（位置参数）→ 默认 `with_pages=True`，
第 2 波接线**无需改动该行**即可拿到正文；`sources` 原样透传，新增的 `page_ok/page_chars` 只是跟随既有 dict 流动
（`raw_title/raw_snippet` 早就在流动，未新增一类风险）。
**PM 已实跑端到端验收通过**（`PM_ACCEPTANCE.md` 判据 1–8），其中判据 1「能搜到真实数据」= 3/3 条正文含真实温度。

## 附录：复跑命令

```powershell
$env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" qq_native_re\findings\search\v1_pages\_v1g_check.py     # 六项证据
& ".\venv\Scripts\python" qq_native_re\findings\search\v1_backend\_v1_drill.py    # 回归演练
```
