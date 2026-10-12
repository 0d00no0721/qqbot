# W4 — 收窄 `error_keywords` 过宽关键词

状态：**已完成**（改动已落盘并验证；**未重启进程**，重启由用户执行）
备份：`qq_native_re/patches/keyword_narrow_backup_20261012-123840/decision_rules.json`（1460 B，改前原文）

## 1. 改动对照（仅 `error_keywords` 一项）

```diff
--- a/config/decision_rules.json
+++ b/config/decision_rules.json
@@ -26 +26,2 @@
-        "error", "exception", "sorry,", "i cannot", "i am unable",
+        "an error occurred", "error occurred", "traceback (most recent call last)",
+        "exception", "sorry,", "i cannot", "i am unable",
```

- 移除 **1** 条：`"error"`；新增 **3** 条：`"an error occurred"`、`"error occurred"`、`"traceback (most recent call last)"`。
- 指定的五个短语里的 `"internal error"`、`"server error"` **改前已逐字存在于清单**（第 13/14 条），故逐字保留、不重复追加 → 清单 20 → **22** 条。
- 其余 19 条（含 4 条刻意的社交话术安全条目）逐字未动，脚本已比对备份核实：`被移除=['error']`、`缺失=[]`。
- `json.load` 校验通过（22 条）。`git diff` 仅此 2 行；同文件里 `api_base_url` 那一处 hunk 是本任务之前就有的未提交改动，非本次所改。

## 2. 命中/不命中实测表（真调 `_validate_content`，原文见 `_w4_kw_after.txt`）

| 用例 | 改前命中 | 改后命中 | 判定/理由 |
|---|---|---|---|
| `terror` | `'error'` | `''` | **有效**（放行） |
| `泰罗奥特曼的 terror 名场面真帅` | `'error'` | `''` | **有效** |
| `error bar` / `ErrorBar` | `'error'` | `''` | **有效** |
| `这个 error 挺奇怪` | `'error'` | `''` | **有效** |
| `这个 ErrorBar 图我画不出来，帮我看下` | `'error'` | `''` | **有效** |
| `Internal error: something went wrong` | `'error'` | `'internal error'` | **无效**：命中错误话术关键词 'internal error' |
| `Server error` | `'error'` | `'server error'` | **无效**：命中 'server error' |
| `An error occurred while processing` | `'error'` | `'an error occurred'` | **无效**：命中 'an error occurred' |
| `Traceback (most recent call last):` | `''`（改前漏网） | `'traceback (most recent call last)'` | **无效**（新增拦截） |
| 正常闲聊句 | `''` | `''` | 有效 |
| 〔探针〕`error` 裸词 | `'error'` | `''` | 有效（**已知代价**，见 §5） |
| 〔探针〕`Error: connection reset by peer` | `'error'` | `''` | 有效（**残留缺口**，见 §5） |

其余 22 条逐条抽验全部仍生效（每条判"无效"且命中的就是它本身），含 4 条社交话术条目 `这个话题我不太了解`/`我们聊点别的吧`/`换个话题吧`/`这个话题我不太清楚`/`我不太了解这个`。脚本 `_w4_kw_check.py` 总体 **ALL PASS**（退出码 0）。

**与委派方预期的出入（如实报）**：提示里怀疑 `这个 error 挺奇怪` 可能命中 `error occurred` —— 实跑**不命中**，判"有效"（该串里没有 `error occurred` 这个连续子串）。

## 3. D6 套件复跑：**63/67 PASS，4 条 FAIL —— 不是 ALL PASS**（如实报，未修脚本）

- 改前基线（同一脚本、同一环境）：**ALL PASS**，日志 `_w4_baseline_before.txt`。
- 改后：`_w4_d6_after.txt`（退出码 2）。67 项检查 **63 PASS / 4 FAIL**，FAIL 全部是**写死了改前"宽 `error`"预期**的旧断言：

| FAIL 项 | 脚本位置 | 性质 |
|---|---|---|
| 用例判定: 错误话术: 纯 error | `w3_d6_fix_check.py:95`（输入 `error`，期望无效） | 旧预期 |
| 用例判定: 错误话术: 句中 Error 大写 | `:96`（`刚才发生了一个 Error 呢`，期望无效） | 旧预期 |
| 用例判定: 误伤观测: terror 内含 error | `:117`（期望无效） | **这正是本任务要消除的误伤** |
| 用例判定: 误伤观测: 英文句子含 error 单词 | `:118`（期望无效） | 同上 |

- **D6 本体未回归**：熔断端到端 11 个 e2e 用例、保真度对比、target 不漂移、配置健壮性共 22 项 **全 PASS**；引擎日志原文仍为
  `[熔断] WARNING: [熔断] 主模型内容异常 (命中错误话术关键词 'internal error')，切换至备用模型` → 主→备降档路径完好。
- 附带影响：脚本的缺陷启发式按"错误话术用例中被判有效的条数"计数（`:160-170`），现在恒有 2 条（纯 error / 句中 Error）被判有效 → 永远打印 `D6 缺陷存在` 并 `exit 2`。属**启发式误报**，非真实缺陷。
- 按红线**未改任何 `.py`**，故这 4 条旧断言保留原样，请 PM 决定是否另开单更新套件预期。

## 4. 热加载判定：**需重启**（非热加载）

代码依据（`scripts/decision_engine.py`）：
- `:197` `self.config = self._load_config(config_path)` → **仅**在 `__init__` 调一次；
- `:217-218` `self._content_checks = failover_cfg.get("content_checks", {})` → 快照进实例属性；
- `:794` `checks = self._content_checks` → 判定时读**内存快照**，`_validate_content` 全文不再碰文件；
- `:228-236` `_load_config()` 里没有任何 mtime/缓存失效逻辑；全仓 `grep DecisionEngine(` 只有 `reverse_bot.py:153`（模块级全局，**进程启动时构造一次**）与 `decision_engine.py:1447`（`if __name__ == "__main__"` 自测块）。运行时无重建路径。
- 实证 `_w4_reload_probe.py`（临时副本，真配置只读，ALL PASS）：同一实例在文件被改后仍按旧 20 条判定（`terror` 仍命中 `'error'`）；用同一文件**新建**实例才切到 22 条（`terror` 放行）→ **重启才生效**。
- 现场：bot PID **30376** 启动于 `2026/10/12 12:11:33`，早于本次改动（12:39）→ 该进程内存里仍是 20 条宽清单，**重启后收窄生效**。

## 5. 已知残留（供 PM 判断，未擅自扩大改动）
1. 裸词 `error` / `Error: connection reset by peer` 之类**不再拦截**（原先靠宽 `error` 兜住）；若在意可后续再加 `error:` 等更具体短语。
2. `"an error occurred"` 是 `"error occurred"` 的超集，功能上冗余（按要求保留两条，命中时报告的是先出现的 `an error occurred`）。

## 6. 声明
- **只改了 `config/decision_rules.json` 的 `error_keywords` 一项**（git diff 仅 2 行）；其它键、其它 19 条逐字未动。
- **未改任何 `.py`**、未重启 PID 30376、未碰 NapCat。
- 本任务新增文件：`qq_native_re/findings/reply_fix/_w4_kw_check.py`、`_w4_reload_probe.py`、`W4_keyword_narrow.md` 及证据文本 `_w4_baseline_before.txt`/`_w4_kw_after.txt`/`_w4_d6_after.txt`/`_w4_reload_probe.txt`/`_w4_kw_results.json`；被复跑脚本自身覆写的是它自己的 `w3_d6_results.json`。
