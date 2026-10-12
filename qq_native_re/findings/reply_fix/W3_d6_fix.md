# W3 / D6 修复报告：`_validate_content()` 真正使用 `error_keywords`

| 项目 | 值 |
|---|---|
| 缺陷 | D6（中危）—— 主模型报错话术被判"内容合法" → 直接发群 **且不降档到备用脚本** |
| 改动文件 | `scripts/decision_engine.py`（唯一改动文件） |
| 备份目录 | `qq_native_re/patches/d6_backup_20261012-113431/decision_engine.py` |
| 备份 sha256 | `52012ea8cab4a5d9cc86de6601d05de7ab4a005c2deeb28ec72b7fcbd05db8bf`（1438 行） |
| 改后 sha256 | `0a13fa29198a62d40e234777fc5fcbc42e1425531fabead6d02155583743816d`（1464 行，+26） |
| 验证脚本 | `qq_native_re/findings/reply_fix/w3_d6_fix_check.py`（42 构造用例 + 7 组配置健壮性 + 11 轮端到端，共 67 项断言） |
| 证据文件 | `_w3_baseline_run.txt`（改前）、`_w3_after_run.txt`（改后）、`_w3_regression_after.txt`、`w3_d6_results.json` |
| 结论 | **ALL PASS**（67/67 断言；改前基线为 33 项 FAIL 且缺陷复现） |

---

## 1. 改动前勘查：`_validate_content()` 实际做了什么

顺序（**改动后完全保持不变**）：

| # | 检查 | 阈值/来源 | 返回 |
|---|---|---|---|
| 1 | 空内容 | `not text or not text.strip()` | `(False, "空内容")` |
| 2 | 过短 | `min_length=3` | `(False, "内容过短 (n < 3)")` |
| 3 | 过长 | `max_length=500` | `(False, "内容过长 (n > 500)")` |
| 4 | 乱码 | `gibberish_max_ratio=0.6`，`_is_gibberish()` | `(False, "疑似乱码")` |
| 5 | ~~错误话术~~ | 第 797 行读了 `error_kws` 却**从未使用** | 直接 `(True, None)` ← **D6** |

改前实测：24/24 条错误话术用例全部判"有效"（`_w3_baseline_run.txt`）。

## 2. `error_keywords` 实际清单 + 误伤风险评估

**配置实际条数 = 20 条（不是任务书里说的 ~21 条，已核实）。** 配置**未增删任何一条**，`git diff` 显示 `config/decision_rules.json` 本任务零改动。

匹配方式是**大小写不敏感子串匹配**（与既有 `_content_filter` 同风格）。因为 `error` 是子串，所以 `terror` / `error bar` 也会命中（已作为"误伤观测"用例实测，见 §3）。

| # | 关键词 | 命中语义 | 误伤风险 | 说明 |
|---|---|---|---|---|
| 1 | `error` | 兜底报错 | **中** | 子串匹配 → `terror`、`mirror`、`error bar`、`Error 404` 都命中；群聊正常回复几乎不含英文 error，但个别玩梗句会 |
| 2 | `exception` | 兜底报错 | 低 | 中文闲聊基本不出现 |
| 3 | `sorry,` | 英文道歉 | **中** | 带逗号才命中；模型正常道歉（"Sorry, 我记错了"）会命中 → 白跑一次备用 |
| 4 | `i cannot` | 拒答 | 低 | |
| 5 | `i am unable` | 拒答 | 低 | |
| 6 | `as an ai` | 模板腔 | 低 | |
| 7 | `i'm sorry` | 英文道歉 | **中** | 与 3 同类 |
| 8 | `i apologize` | 英文道歉 | 低 | |
| 9 | `unable to` | 失败 | 低 | |
| 10 | `could not` | 失败 | 低 | |
| 11 | `internal error` | 兜底报错 | 低 | 被 1 覆盖 |
| 12 | `server error` | 网关报错 | 低 | 被 1 覆盖 |
| 13 | `request failed` | 网关报错 | 低 | |
| 14 | `bad request` | 网关报错 | 低 | |
| 15 | `invalid request` | 网关报错 | 低 | |
| 16 | `这个话题我不太了解` | 旧 persona 安全话术 | 低 | **刻意设计**（见 `_SENSITIVE_KEYWORDS` 上方注释） |
| 17 | `我们聊点别的吧` | 话题转移 | **中高** | 群聊里这是**正常**接话，模型可能主动这么说 → 白跑一次备用 |
| 18 | `换个话题吧` | 话题转移 | **中高** | 同上 |
| 19 | `这个话题我不太清楚` | 话题转移 | **中** | 同上 |
| 20 | `我不太了解这个` | 话题转移 | **中** | 同上 |

### 误伤的真实代价（重要，请 PM 据此定夺）

命中关键词的后果**不是丢消息**，而是走既有熔断：**多调一次备用脚本**。
- 备用正常 → 用户收到备用回复（内容不同，但不会发报错话术）；
- 备用也命中 → `放弃回复`（该条静默）——这才是唯一"实质损失"。

因此 17~20 这四条"正常句式"最坏情况是：主模型说了"我们聊点别的吧"，被判异常 → 备用模型重答一次；若备用也说同类话，则本轮静默。

**建议（供 PM 决策，我未擅自改动配置）**：
1. `error`（1 号）建议改成更具体的组合或加词边界（如 `\berror\b` / `internal error`+`server error`），否则 `terror`/`mirror` 类英文会误判；
2. 17~20 四条"话题转移"句是否保留，取决于产品意图——它们既是"模型拒答"的信号，也是**正常社交话术**；若要保留拒答语义又不想误伤，建议 PM 决定是否拆成独立开关（例如只对英文兜底话术熔断）。

## 3. 改动对照（改动前 → 改动后）

唯一改动：`_validate_content()` 末尾在**既有 4 项检查之后**插入错误话术检测 + 新增一个静态辅助方法。其余检查的语义与顺序、`target`/`avoid_recent` 相关代码**零改动**。

```diff
--- a/scripts/decision_engine.py
+++ b/scripts/decision_engine.py
@@ -813,7 +813,33 @@
         if self._is_gibberish(text_stripped, gibberish_ratio):
             return (False, "疑似乱码")
 
+        # 错误话术检测（D6 修复）
+        # error_keywords 是"模型没答上来"的兜底话术（Internal error / I cannot /
+        # 这个话题我不太了解 …）。命中即判内容异常 → 上游 _generate_reply_with_failover
+        # 走既有熔断路径（切备用脚本，备用也异常则放弃回复），避免把报错话术发到群里。
+        # 判定方式与 _content_filter 同风格（strip + 小写化后子串匹配），大小写不敏感。
+        hit = self._match_error_keyword(text_stripped, error_kws)
+        if hit:
+            return (False, f"命中错误话术关键词 '{hit}'")
+
         return (True, None)
+
+    @staticmethod
+    def _match_error_keyword(text: str, error_keywords) -> str:
+        """返回命中的第一个错误关键词（无命中/配置异常返回 ""）。
+
+        大小写不敏感的子串匹配（与 _content_filter 同风格）。配置为空或不是列表时
+        直接放行——配置缺项不该让机器人整体不回复。
+        """
+        if not error_keywords or not isinstance(error_keywords, (list, tuple)):
+            return ""
+        text_lower = text.lower()
+        for kw in error_keywords:
+            if not isinstance(kw, str) or not kw:
+                continue
+            if kw.lower() in text_lower:
+                return kw
+        return ""
```

设计要点：
- **不加新的日志/打印**——原因串由既有 `_generate_reply_with_failover` 的 `logger.warning("[熔断] 主模型内容异常 (%s)…")` 输出，零重复日志；
- **reason 带命中词**（`命中错误话术关键词 'error'`）便于线上定位误伤；
- 配置缺项/类型异常**直接放行**（宁可漏熔断也不让机器人整体不回复），已用 7 组异常配置实测不抛异常。

## 4. 构造用例结果表（42 例；`_w3_after_run.txt`）

### 4.1 D6 恢复目标：24 条错误话术，改前 24/24 判有效 → 改后 24/24 判无效

| 输入 | 改前 | 改后判定 + 理由 |
|---|---|---|
| `Internal error: something went wrong` | 有效 ❌ | **无效** / 命中 `'error'` |
| `internal error, please try again` | 有效 ❌ | **无效** / 命中 `'error'` |
| `抱歉，刚才 internal error 了` | 有效 ❌ | **无效** / 命中 `'error'` |
| `error` | 有效 ❌ | **无效** / 命中 `'error'` |
| `刚才发生了一个 Error 呢` | 有效 ❌ | **无效** / 命中 `'error'`（大小写不敏感） |
| `Server error 500 - gateway timeout` | 有效 ❌ | **无效** / 命中 `'error'` |
| `request failed, please retry later` | 有效 ❌ | **无效** / 命中 `'request failed'` |
| `bad request: invalid parameter` | 有效 ❌ | **无效** / 命中 `'bad request'` |
| `Traceback exception occurred` | 有效 ❌ | **无效** / 命中 `'exception'` |
| `Sorry, I cannot do that right now` | 有效 ❌ | **无效** / 命中 `'sorry,'` |
| `I cannot answer that question` | 有效 ❌ | **无效** / 命中 `'i cannot'` |
| `I am unable to respond` | 有效 ❌ | **无效** / 命中 `'i am unable'` |
| `As an AI language model, I must decline` | 有效 ❌ | **无效** / 命中 `'as an ai'` |
| `I'm sorry, something broke` | 有效 ❌ | **无效** / 命中 `'sorry,'` |
| `I apologize for the confusion` | 有效 ❌ | **无效** / 命中 `'i apologize'` |
| `unable to process your request` | 有效 ❌ | **无效** / 命中 `'unable to'` |
| `could not reach the upstream model` | 有效 ❌ | **无效** / 命中 `'could not'` |
| `这个话题我不太了解` | 有效 ❌ | **无效** / 命中同名 |
| `我们聊点别的吧` | 有效 ❌ | **无效** / 命中同名 |
| `换个话题吧` | 有效 ❌ | **无效** / 命中同名 |
| `这个话题我不太清楚` | 有效 ❌ | **无效** / 命中同名 |
| `我不太了解这个，换个问题？` | 有效 ❌ | **无效** / 命中 `'我不太了解这个'` |
| `INTERNAL ERROR` | 有效 ❌ | **无效** / 命中 `'error'` |
| `ERROR: exception, request failed` | 有效 ❌ | **无效** / 命中 `'error'`（返回首个命中） |

### 4.2 误伤观测（明确记录，不改配置）

| 输入 | 改后判定 | 备注 |
|---|---|---|
| `泰罗奥特曼的 terror 名场面真帅` | **无效**（命中 `'error'`） | 子串匹配的已知代价 |
| `The inflation rate is my favorite error bar` | **无效**（命中 `'error'`） | 同上 |

> 代价 = 多调一次备用脚本，不会发错内容；见 §2 建议。

### 4.3 正常回复仍有效 + 既有边界行为逐条不变

| 输入 | 改前 | 改后 |
|---|---|---|
| `今天天气不错，要不要一起出去走走？` | 有效 | **有效**（无变化）|
| `抱歉，我现在无法回答` | 有效 | **有效**（无变化）|
| `Nice weather today, let's go out` | 有效 | **有效** |
| 100 字长文本 / `在的呀`(恰好 3 字) / `https://example.com/a?b=c` / `12345` / `😀 好耶` | 有效 | **有效**（逐条无变化）|
| `""` / `"   "` / `"\n\t "` | 无效 `空内容` | **无效 `空内容`**（同）|
| `"好吧"`（2 字） | 无效 `内容过短 (2 < 3)` | **无效 `内容过短 (2 < 3)`**（同）|
| 600 字长文本 | 无效 `内容过长 (600 > 500)` | **无效 `内容过长 (600 > 500)`**（同）|
| `😀😀😀😀😀😀` | 无效 `疑似乱码` | **无效 `疑似乱码`**（同）|
| 私用区乱码 ×20 | 无效 `疑似乱码` | **无效 `疑似乱码`**（同）|
| 含 `_SENSITIVE_KEYWORDS` 的回复 | `_validate_content` 有效（由 `_content_filter` 拦截） | **有效**（同，语义未越界）|

### 4.4 配置异常健壮性（新增防护，7/7 PASS）

`None` / `[]` / `"not-a-list"` / `123` / `[None,123,"","error"]` / `("exception",)` / `{"a":1}` → 均返回字符串、**不抛异常**；`[None,123,"","error"]` 正确跳过非法项并命中 `'error'`。

## 5. 端到端降档证据（真子进程 + 真脚本 + 假模型池，零 API 费用）

链路：`_generate_reply_with_failover`（**真实、未改动**）→ `scripts/model_primary.py`/`model_fallback.py`（**真实脚本**，经 `_d6_e2e/scripts/` 桩入口 `runpy` 执行）→ 假 `llm_pool.chat`（按预置队列返回文本）。

### 场景 A：主模型吐报错 → 降档到备用，最终回正常内容 ✅
```
预置队列        : ['Internal error: something went wrong', '今天天气不错，要不要一起出去走走？']
子进程调用序列  : ['model_primary', 'model_fallback']   (共 2 次)   ← 改动前只有 1 次
最终返回        : '今天天气不错，要不要一起出去走走？'              ← 改动前是 'Internal error: …'
引擎熔断日志    :
  [熔断] INFO: [调试] _call_model_script 被调用: script=model_primary_stub.py, gid=99999
  [熔断] WARNING: [熔断] 主模型内容异常 (命中错误话术关键词 'error')，切换至备用模型 | 回复内容: Internal error: something went wrong
  [熔断] INFO: [熔断] 调用备用模型（独立进程）
  [熔断] INFO: [调试] _call_model_script 被调用: script=model_fallback_stub.py, gid=99999
  [熔断] INFO: [熔断] 备用模型回复有效
```
**改动前同一场景（`_w3_baseline_run.txt`）**：序列 `['model_primary']`，最终返回 `'Internal error: something went wrong'` —— 报错话术原样发群，熔断完全没触发。

### 场景 B：主备都吐报错 → 放弃回复（不发错话） ✅
```
预置队列 : ['Internal error: something went wrong', 'Internal error: fallback also broken']
序列     : ['model_primary', 'model_fallback']   最终返回: ''
日志     : [熔断] 主模型内容异常 (命中错误话术关键词 'error')，切换至备用模型
           [熔断] 备用模型内容异常 (命中错误话术关键词 'error')，放弃回复
```

### 场景 C/D：正常路径无回归 ✅
```
['今天天气不错…']       → 序列 ['model_primary']                    最终 '今天天气不错…'（不降档）
['', '今天天气不错…']   → 序列 ['model_primary','model_fallback']   最终 '今天天气不错…'（空回复降档语义不变）
[报错, 正常] + 显式 target → 主备收到**同一个** target（无漂移）
```

### 测试保真度（本机沙箱限制的诚实说明）
本机 DSH 沙箱下 `asyncio.create_subprocess_exec` 抛 `WinError 5`（管道→IOCP 被拒），**生产路径的起进程方式在本沙箱内无法执行**。为此测试用了一个同步等价实现 `_sync_call`，并做了两级保真度校验：
1. **stdin payload 与引擎原实现逐键完全一致**（用 `json.dumps` 间谍截获引擎 `_call_model_script` 真实组装的那份 payload 做对比）→ 2/2 PASS；
2. 同一场景重复两轮结果稳定（真脚本 + 假模型池可复现）→ 3/3 PASS。

即：**判定逻辑、熔断编排、真脚本的 prompt 组装全部是真实代码**；只有"起子进程的 IO 方式"是等价替换。

## 6. 回归结果（全部 exit=0）

| 套件 | 结果 |
|---|---|
| `w1_prompt_baseline_check.py` | **ALL PASS** / EXIT=0 |
| `w1_assembly_check.py` | **ALL PASS** / EXIT=0 |
| `w1_defect_fix_check.py` | **ALL PASS** / EXIT=0（W1 的 D1/D2/D4 未回归）|
| `scripts/test_llm_pool.py` | **全部通过** / EXIT=0（A/B/C/D 四场景；含真实网关调用，受限于"必须复跑"的要求而执行）|
| `python -c "import reverse_bot; print('OK')"` | **OK** / EXIT=0 |
| `w3_d6_fix_check.py`（本文套件） | **ALL PASS** 67/67 断言 / EXIT=0 |

原始输出：`_w3_regression_after.txt`。

## 7. 声明与遗留

- **只改了 `scripts/decision_engine.py` 一个文件**（+26 行：`_validate_content` 内 6 行 + 新增 `_match_error_keyword` 20 行）。
- **未增删任何 `error_keywords` 配置条目**，`config/decision_rules.json` 本任务零改动（git 里它的 `M` 状态是任务开始前就有的）。
- **未触碰**：`reverse_bot.py`、`model_primary.py`/`model_fallback.py`、`.env`、persona、模型池配置、`target`/`avoid_recent` 相关代码、NapCat。
- **未重启 bot**（PID 8272 于 08:15:16 启动，`netstat` 确认仍在 `0.0.0.0:8080` LISTENING，**仍在跑旧代码**）。要让修复生效需用户重启。
- 沙箱相关的操作限制（若 PM 要复现）：本会话内 `asyncio.create_subprocess_exec` 被 DSH 沙箱拒绝、`Get-Process -Name python` 列表被沙箱遮挡（`netstat`/按 PID 查询正常）；因此 E2E 走同步等价实现，已按上文做了保真度证明。
- 遗留建议（见 §2）：`error` 子串过宽 + 4 条"话题转移"句的误伤，需 PM 决定是否调整配置；我未擅自改动。
