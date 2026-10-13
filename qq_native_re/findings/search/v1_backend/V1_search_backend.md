# V1 搜索后端 + 三层判定器 —— 交付报告

- 日期：2026-10-13
- 执行：实现子 agent（h-1 `search-backend`，代码在内存中写完并实测后由 PM 原样落盘）
- 落盘：PM（因该子 agent 会话为只读沙箱，无法自行落盘；PM 逐字转录，落盘后**亲自复跑演练 exit=0**）

## 交付物

| 文件 | 说明 |
|---|---|
| `scripts/web_search.py`（6,874 B） | bridge 客户端：`search(query, max_results=5, time_range=None)` / `ping()`；永不抛异常；clamp 1..10；15s 超时（`DSH_SEARCH_TIMEOUT` 可覆盖）；base_url 优先 `DSH_SEARCH_BASE_URL` > `config/search.json` > 默认 |
| `scripts/search_gate.py`（10,108 B） | 三层判定 `decide(text, gid, *, feature_on=True)`：T0 显式 → T1 词表+疑问 → T2 路由器 → 默认不搜；每群 10 次/小时滑动窗口；`config/search.json` mtime 热加载；`load_config()` / `_rate_reset()` 公开供上层与演练使用 |
| `config/search.json` | `enabled/tiers(t0,t1,t2)/rate_limit_per_hour=10/max_results=5/timeout_seconds=15/router_max_tokens=24/router_timeout_seconds=8/interim_notice=false/base_url=""` |
| `_v1_drill.py`（8,227 B） | 可复跑演练（6 项），PM 复跑 **exit 0、硬失败 0 条** |

## 实测证据（PM 于 2026-10-13 06:41 复跑原文摘录）

```
bridge ping → True bridge 可达（search-rejected）
1) 实弹搜索: ok=True provider=bing elapsed=259ms cache=hit sources=3
2) 降级: 死端口 → ok=False reason=ConnectError（2,310ms，未抛异常）；ping=False
        超时 50ms → ok=False reason=ReadTimeout（324ms，未抛异常）
3) T0/T1 判定表 12/12 [OK]（T0 两条查询词剥离正确；T1 命中 天气+怎么 / 最新+多少 / 股价 / 谁赢了；6 条负例全部不搜）
4) T2 实弹 5/5 [OK]（raw='SEARCH: React 19 破坏性变更'、'SEARCH: Vue 3.5 相比 3.4 的变化'，model=deepseek-flash；3 条 'NO'）
   端到端：未命中 T0/T1 的消息确实走 T2
5) 限流：同群 11 次 → 前 10 放行，第 11 次被拒
6) fail-closed：池异常/乱码/NO/空输出 → 全部不搜；SEARCH: → 搜
exit=0
```

## 已知待改进项（PM 实测发现，将另开补丁任务）

1. **snippet/title 未做 HTML 实体解码**（真实数据含 `&ensp;`/`&middot;`）→ 注入 prompt 前会带实体噪声
2. **未剥离引用块**（真实格式 `[回复消息 [昵称(QQ号)] 被引用内容]`，仅一个收尾 `]`）→ 引用他人消息时可能按对方的话误触发
3. **T1 词表偏宽**（含 `最新/今天/现在/价格/版本号`）：PM 用 135 条真实群消息回放，宽版出现 1 次 100% 误报
4. **`decide()` 返回值缺 `time_range`**（PM 原计划让 T1 命中"今天/最新"时传 `day`，实测该参数会让 bridge 自动换到 exa 引擎，可用）

## 未动既有文件声明

本次仅新增上述 4 个文件；未改 `llm_pool.py` / `_state.py` / `decision_engine.py` / `model_*.py` / `reverse_bot.py` / `.env` / NapCat；未重启任何进程。
