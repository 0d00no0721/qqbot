# W1 定向回复修复 — 独立对抗性核实报告

核实人：独立验证者（未参考 `w1_prompt_baseline_check.py` 的结论）
时间：2026-10-12 10:51 – 11:20
工作目录：`E:\QQbot`；Python `.\venv\Scripts\python` = 3.14.3

## 0. 重要前提：核实期间源码被并发修改（三个不同工作流同时在改盘）

| 文件 | 我读到的版本 | 核实期间的变化 |
|---|---|---|
| `scripts/model_primary.py` | 10:41:59 | **11:00:53 被改**（路 B 追加一段「不要编造实时信息」） |
| `scripts/model_fallback.py` | 10:42:39 | **11:00:53 被改**（同上） |
| `scripts/llm_pool.py` | — | **11:08:05 被改**（V1 追加 `vision` 参数） |
| `config/model_pool.json` | — | **11:07:04 被改**（新增 `vision_models` 键） |
| `scripts/test_pool_harden.py` | — | **11:11:59、11:15+ 被反复改** |
| `scripts/vision.py` / `image_utils.py` | 不存在 | **11:09:26 / 11:08:59 新建** |

**本报告所有结论对应的字节（末次复跑，SHA256[:16]）：**

```
73E1CFBCD2E7349A  model_primary.py
B021DFD8737D5482  model_fallback.py
78BF417FE882EEAF  decision_engine.py
```
`_verify_p3_stdin.py` 内置前后哈希自检，末次运行前后一致；`_verify_p1_ast.py` /
`_verify_p4_engine.py` 在稳定期复跑均为 `"fails": []`。

## 1. 逐条结论

| # | 断言 | 结论 |
|---|---|---|
| 1 | 无 target 时 prompt 与改动前逐字相同 | **PASS** |
| 2 | 恰好在带 target 时才走新 prompt | **PASS（prompt 层面）/ FAIL（stderr 诊断行）** |
| 3 | target 贯穿整条调用链 | **PASS** |
| 4 | 防复读队列正确性 | **PASS** |
| 5 | 两个回归脚本仍全过 | **FAIL**（但**不是**本修复造成，见 §7） |

## 2. 断言 1 — PASS

方法：用 `ast` 从改前备份里抠出 `generate_reply` 内联 prompt **表达式的源码片段**，
再用 ast 从改后 `_build_prompt` 抠出「路 A」的 `return` 表达式源码片段，
比较 `ast.dump`（parse 规则）→ 相同；比较 `ast.unparse`（规范化文本）→ 相同；
在只有 `context` 一个变量的命名空间里 eval 两段，对 24 个 context 取值
（空串/纯空白/2 万字符/CQ 码/emoji/`\r\n`/花括号/引号/零宽字符…）比较 `==`
与 UTF-8 字节 → 全部相同。两个文件（primary / fallback）都验。

命令：`& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p1_ast.py`

```
  [PASS] model_primary.py: 改前内联 prompt 与改后路A 的 ast.dump 完全相同
  [PASS] model_primary.py: 规范化（ast.unparse）后的源码文本完全相同
  [PASS] model_primary.py: 24 个 context 取值上一律 == 且 UTF-8 字节相同
  [PASS] model_primary.py: target 缺省 None / {} / content='' / '   ' / None / 0
         → _build_prompt == 改前内联（110 字符）
  …… model_fallback.py 同样全部 PASS
  "fails": []
```
（11:00:53 那次改动只动了路 B，路 A 仍逐字相同 → 结论对当前字节依然成立。）

## 3. 断言 2 — 主结论 PASS，但发现 1 个真实缺陷

方法：`_verify_stub_env/` 放一个离线 `llm_pool` 桩，
并用 `sitecustomize.py` 在解释器启动早期把它塞进 `sys.modules` 的
`llm_pool` **和** `scripts.llm_pool` 两个名字——因为直接跑
`python scripts\model_primary.py` 时 `sys.path[0]` 是 `E:\QQbot\scripts`，
脚本里第一条 `from scripts.llm_pool import chat` 会命中**真实**的
`scripts/llm_pool.py`（我第一次探针就是这样打到了真实网关并拿到了真实天气回复）。
桩把 `messages` 落盘，于是我拿到「脚本真正发出去的 user prompt」原文。

期望值不手抄：路 A 期望 = 改前备份片段的 eval 结果；路 B 期望 = **磁盘现场**
`_build_prompt` 的返回值。35 个输入形态 × 2 个脚本：

```
  [PASS] 缺 target 键 / target=null / {} / content='' / '   ' / '\n\t ' / None / 0
         / False / [] / {} / 缺 content 键 / 'abc' / ['a','b'] / 123 / True
         / [{'content':'hi'}] → 全部走旧 prompt（len=110）
  [PASS] 昵称空串 / 缺 nickname / nickname=None / 正常 target / content 前后空白
         / content=int / dict / list / CQ 图片码 / CQ json 码 / 超长 5w 字符
         → 全部走新 prompt，实发 prompt 与期望逐字相同
  [PASS] 畸形 stdin（非 JSON / 空）→ 优雅返回 {"reply": ""}，不打路由行
  [PASS] stderr 判定与实发 prompt 形态一致（不一致 0 例）
```

- 无「有 target 却走旧 prompt」的形态（`content` 非空必走新路）。
- 无「无 target 却走新 prompt」的形态。
- 注意：`target` 非 dict（字符串/列表/数字/布尔）在 `main()` 里被归一化为
  `None` → 走旧 prompt **正确**；`[{'content':'hi'}]` 这类"非 dict 但内容为真"
  也被归一化掉 → 旧 prompt，与「target 必须是 dict」的契约一致。

### 发现的真实缺陷 D1（低危，建议修）

**日志判定用的是未 strip 的原始值，与 `_build_prompt` 的判定不一致。**

```python
# model_primary.py 183 / model_fallback.py 196
print(f"[model_primary] 定向回复={'是' if (target or {}).get('content') else '否'}"
      f" | 防复读注入={len(avoid_recent)}条", ...)
```
```python
# _build_prompt 第 75 行：先 strip 再判空
target_content = str(target.get("content") or "").strip()
if not target_content:   # ← 纯空白走旧 prompt
```

实测：`target={"content":"   "}` 与 `{"content":"\n\t "}` →
stderr 打 `定向回复=是`，但**实发的是旧 prompt**（桩抓到的 user_prompt == 110 字符旧文案）。

```
  [FAIL] model_primary.py | target={'content':'   '}: stderr 判定=是（期望 否）| [model_primary] 定向回复=是
  [FAIL] model_primary.py | target={'content':'\n\t '}: stderr 判定=是（期望 否）| …定向回复=是
  [FAIL] model_fallback.py | target={'content':'   '}: stderr 判定=是（期望 否）
  [FAIL] model_fallback.py | target={'content':'\n\t '}: stderr 判定=是（期望 否）
```
影响：**只影响可观测性，不改变行为**。但「定向回复=是/否」正是运维/回放用来
判断走哪条 prompt 的行，它对纯空白 content 会撒谎。
另：`防复读注入=N条` 数的是 `avoid_recent` 的原始长度，不是实际注入条数
（例：`['', null, '  ', '真话']` 打印 `注入=4条`，实际只注入 1 条；且空串会被
`str(r).strip()` 过滤，`None` 反而不被过滤，见 D3）。

## 4. 断言 3 — PASS

方法：进程内把 `DecisionEngine._call_model_script` 换成打桩，捕获真实下发的参数。

```
  [PASS] @bot 只调用一次子进程；target == {"nickname":"小明","uid":"12345","content":"今日深圳天气"}
         （nickname 去空白、user_id 字符串化、content 去空白，均等于入参）
  [PASS] 主失败→备：两次调用拿到同一个 target（降档不漂移）
  [PASS] sender_nickname='' → target.nickname=''（不是缺键）
  [PASS] 非@ 路径（is_at_bot=False，分类器判定回复）**同样下发了 target**
         = 触发回复的那条消息；debug["回应目标"]="小刚(888)"
  [PASS] 判定不回复时不产生任何子进程调用
```

## 5. 断言 4 — PASS（含对抗点）

```
  [PASS] 第1轮下发 avoid_recent 为空（不含本轮回复自身）
  [PASS] 第2轮下发 == 只有第1轮回复（生成前读取）
  [PASS] 第3轮下发 == 前两轮；第4轮下发 == 第1~3轮，本轮第4条不在内
  [PASS] _recent_bot_replies maxlen == 3；ANTI_REPEAT_WINDOW == 3；队列长度恒 ≤3
  [PASS] _record_bot_reply_metric 返回 None（无返回值可被调用方当拦截信号）
  [PASS] 记录一条 100% 重复的回复后队列长度不变（只写指标）
  [PASS] 与历史回复 100% 相似时仍照常返回回复（同群、@bot 路径）
```
`_record_bot_reply_metric` 体内没有任何 `return` 影响调用方（源码 L994-1012，
只有 logger 与 `append`）。全仓 grep 确认它只有 2 个调用点（L965、L983，
各自 `return reply` 之前），`_recent_bot_replies` 只有 L885 读、L1012 写。

**对抗点（主失败→备成功会不会重复 append）：**
```
  [PASS] 主成功:            metric 调用 1 次，队列 =["主回复OK"]
  [PASS] 主空→备成功:       metric 调用 1 次，队列 =["备回复OK"]（只 append 一次）
  [PASS] 主内容异常→备成功: metric 调用 1 次
```
因为主模型成功路径是 `_record_bot_reply_metric(...); return reply`，
走到备用分支时主分支已经 return，不存在两路都 append。

## 6. 主动发现的其它缺陷

### D2（低危·现存·可达）@bot + 纯 CQ 消息时，target.content 是 CQ 码原文
`reverse_bot.py` L260 的守卫 `if not clean_message: return` 用的是 **strip 之前**
的 `clean_message`（`re.sub` 结果未再 strip 就判空），所以
`raw_message = "[CQ:at,qq=BOT] "` 这种 @bot 后跟空白/表情的消息会走
`clean_or_raw = raw_message`，而 `is_at_bot` 仍为 True。实测真实引擎：

```
      → 正常返回 (True, '…') | 调用数=1
      （真实链路：clean_message='' → clean_or_raw='[CQ:at,qq=2668851638]' is_at_bot=True）
```
于是 prompt 变成：
```
【你需要回应的最新消息】小明: [CQ:at,qq=2668851638]
```
即「请针对这条消息作答」，而这条消息只是一个 CQ 码——而 prompt 紧接着又要求
「不要包含 CQ 码」。这是本修复**新引入**的形态（改动前没有这行，模型只看到背景）。
同理，`is_at_bot=False` 且消息是纯图片时（`clean_message` 为空、分类器恰好判需要回复），
`target.content` 会是整条 `[CQ:image,file=…,url=http://gchat.qpic.cn/…]`（实测可到 100+ 字符）。
建议：`_build_target` 里剥 CQ 码，或对 `[CQ:` 开头的 content 退化为 `None`。

### D3（低危·潜在）`avoid_recent` 里的 `None` 会被注入成字面量 `- None`
```python
recent = [str(r).strip() for r in (avoid_recent or []) if str(r).strip()]
```
`str(None).strip()` = `'None'` 为真 → 不过滤。实测：
```
  avoid_recent=['', None, '  ', '真话'] → 注入段='\n- None\n- 真话\n不要重复这些内容…'
  avoid_recent=[None]                   → 注入段='\n- None\n…'
```
上游 `decision_engine` L885 用 `[r for r in … if r]` 过滤，不会下发 `None`，
所以当前**不可达**；但子进程是独立契约边界，任何调用方传 `None` 都会污染 prompt。
修法：`if isinstance(r, str) and r.strip()`。

### D4（低危）`_build_target` 无类型守卫，`should_reply(message=…)` 形参无强制
```
  [RAISE] int 42   → AttributeError: 'int' object has no attribute 'strip'
  [RAISE] bytes    → TypeError: Object of type bytes is not JSON serializable
  [RAISE] list     → AttributeError: 'list' object has no attribute 'strip'
  [RAISE] dict     → AttributeError: 'dict' object has no attribute 'strip'
  [ OK ]  空串/纯空白/None → 返回 None
```
另一半：`_build_target` 返回 `None` 时调用方**不解引用保护**：
```
      message='   ' + is_at_bot=True → 抛 TypeError: 'NoneType' object is not subscriptable
      message=42    + is_at_bot=True → 抛 AttributeError: 'int' object has no attribute 'strip'
      message=None  + is_at_bot=True → 抛 TypeError: 'NoneType' object is not subscriptable
```
（崩在 `decision_engine.py` L1271/L1347 的 `target['nickname']`。）
**可达性结论**：按当前 `reverse_bot.py` 链路，
`is_at_bot=True` 时 `message` 恒为非空字符串（L260 守卫 + L291 `clean_or_raw`），
所以 None/int/纯空白这三种**当前不可达**，属防御性缺口；
异常会被 `reverse_bot.py` L336 的 try/except 兜住（只记一条「[决策引擎] 出错」），
不会打死机器人，但对用户表现为「@了机器人却没反应」。

### D5（低危·设计取舍）target.content 与 context 重复出现
`reverse_bot` 先 `add_message(...)` 再 `should_reply(...)`，所以被回应的那条消息
同时出现在【你需要回应的最新消息】和群聊背景里：
```
  同一句话在 prompt 中出现次数 = 2
```
单条消息体积约翻倍；若消息很长（NapCat 上限级），prompt 会被放大。

### D6（已评估·不担心）空洞 prompt 与超长 prompt
昵称缺失时的真实 prompt：
```
【你需要回应的最新消息】: 今日深圳天气
```
只差冒号前一个昵称，语义仍完整（没有变成 `【…】: ` 后接背景），
模型看到的是「有一条最新消息、发信人未知」。**判断：不值得担心**，
但若想更稳，可退化为 `【你需要回应的最新消息】某群友: …`。

超长规模实测（`avoid_recent` 当前受 `max_length=500` 与 maxlen=3 约束 → ≤1500 字符，安全）：
```
  target.content=  20000 字符 → prompt= 21482 字符 (+20181)
  target.content=  50000 字符 → prompt= 51482 字符 (+50181)
  avoid_recent=3×  50000 字符 → prompt=151523 字符 (+150222)   ← 仅当上游不设限
```
真正的膨胀源是 `target.content`（`avoid_recent` 有 500 字符上界兜底）。
但实测 `_validate_content` 的 `error_keywords` **根本没被使用**（见下），
`max_length=500` 只作用于模型**输出**，所以「模型不吐超长」这一层是有效的。

### D7（中危·现存·与本修复无关）`_validate_content` 读了 `error_keywords` 却从不使用
```python
error_kws = checks.get("error_keywords", [])   # L792，读了但下文再没出现
```
```
  'Internal error: 模型超时了' -> (True, None)
  '这个话题我不太了解'          -> (True, None)
  'an error occurred'          -> (True, None)
```
后果：主模型若返回 `Internal error: ...` / `as an AI ...` 这类兜底话术，
`_generate_reply_with_failover` 会判「主模型回复有效」→ 直接发给群友，
**备用模型根本不会启动**。实测：`script_calls=["model_primary.py"]`, 队列 =
`['Internal error: 模型超时了']`。
`qq_native_re/patches/reply_fix_backup_20261012-103755/decision_engine.py` 里该函数
逐字相同 → 是**改动前就存在**的问题，不是本修复引入，但它让「熔断」这一层形同虚设。

### D8（结论·不担心）编码 / stdin 规模
```
  ensure_ascii=False → 833 字符 / 2069 字节（UTF-8 往返一致=True）
  '😀🎉' / '𠮷' / '\u200b' / '﷽' → prompt 中 UTF-8 往返均一致
```
`json.dumps(..., ensure_ascii=False)` 后 `encode("utf-8")`，
子进程 `sys.stdin.buffer.read().decode("utf-8")` —— 两侧对称，
无 surrogate（4 字节 emoji、生僻字、零宽字符实测无损坏）。
当前 payload 2KB 级，距 PIPE 缓冲/命令行上限极远，**无超限风险**。

## 7. 断言 5 — FAIL，但根因不是本修复（两个脚本都被并发改写中）

### `scripts/test_llm_pool.py` — 全部通过（exit 0）
```
  环境: LLM_POOL_DISABLE = None
  [PASS] 实际服务模型落在 T1（['deepseek-flash','deepseek-flash-2']）: deepseek-flash
  [PASS] 拿到了非空回复 / 没有落在那个不存在的模型上 / 降档后依然拿到了非空回复
  [PASS] 抛出了 LLMPoolExhausted / 消息里能看到每个模型的失败原因 / 审计日志里有记录
  结果: 全部通过
```
（串行跑，未与 harden 并行；两次复跑均通过。）

**前置修正**：任务描述里说「这两个脚本自身不调 `load_dotenv()`，需先注入 `.env`」
——`llm_pool.py` **自己会加载**（L63-68：`from dotenv import load_dotenv;
_load_dotenv(PROJECT_ROOT/".env", override=False)`）。我的运行**没有**注入任何
环境变量，A 场景照样拿到了真实回复与真实 base_url，说明 `.env` 已被自动加载。
这一条前置假设不成立。

### `scripts/test_pool_harden.py` — exit 1 / 8 项失败，**根因是测试脚本自身的比较式写错了**

```
  [FAIL] available_models 签名与 W1 一致
  [FAIL] load_config 签名与 W1 一致
  [FAIL] cooldown_snapshot 签名与 W1 一致
  [FAIL] clear_cooldowns 签名与 W1 一致
  [FAIL] audit_log_path 签名与 W1 一致
  [FAIL] get_active_model 签名与 W1 一致
  [FAIL] chat / chat_sync 签名与 W1 一致（剔除有意新增的 ['vision'] 后）
```
我逐字复刻它的比对流程（`_verify_p6_replicate_h6.py`）得到：
```
  available_models:
      str(sig_old) = "() -> 'list[str]'"
      sig_new      = "() -> 'list[str]'"
      str(sig_old) == str(sig_new)  = True
      【测试脚本实际用的式子 sig_old == sig_new】= False   ← 对每一个函数都 False
```
原因：测试脚本 L811-825 先把 `sig_new` 变成了**字符串**
（`sig_new = str(sig_new_raw.replace(...))`），却拿它和 `Signature` 对象做
`sig_old == sig_new`。`inspect.Signature` 的 `__eq__` 对非 Signature 恒为 False：
```
  Signature 是否自定义 __eq__: True
  同一函数两次取签名 == : True
  签名 == 自己的字符串 : False      ← 就是这个
  字符串 == 字符串     : True
```
我把注解归一化成字符串后逐项比对，**8 个签名全部一致**（`chat`/`chat_sync`
剔除末尾的 `vision: bool = False` 后与 W1 基准逐字相同）：
```
  chat: 归一化后一致 = True
  chat_sync: 归一化后一致 = True
  available_models: True   load_config: True   cooldown_snapshot: True
  clear_cooldowns: True    audit_log_path: True  get_active_model: True
```
**归属**：这 8 项的差异（`vision` 关键字参数）来自并发的 V1 视觉工作流
（`llm_pool.py` 11:08:05、`test_pool_harden.py` 11:11:59/11:15+ 被反复改）。
`vision` 是**末尾 keyword-only、默认 False**，`model_primary.py` /
`model_fallback.py` 里 `"vision"` 出现次数为 **0**，`chat()` 调用参数与改动前
逐字相同（`label/max_tokens/temperature/preferred/timeout`）→
**W1 的模型调用契约未受影响**。
附带事实：`test_pool_harden.py` 在我核实期间被改了至少 3 次
（11:10 那次是 2 项失败、11:12 起变成 8 项），**这也说明该脚本不是稳定判据**。

## 8. 复现命令

```powershell
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p1_ast.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p3_stdin.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p4_engine.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p5_edge.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p6_harden_h6.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p6_signature_rootcause.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_verify_p6_replicate_h6.py
& ".\venv\Scripts\python" scripts\test_llm_pool.py
& ".\venv\Scripts\python" scripts\test_pool_harden.py
```
未修改 `scripts/` 下任何文件；全部脚本以 `_verify_` 前缀放在
`qq_native_re\findings\reply_fix\` 下。
