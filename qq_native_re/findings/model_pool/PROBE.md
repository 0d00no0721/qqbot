# 本地网关模型能力探针（127.0.0.1:8090）

> 探针执行者：探针子 agent（PM 委派）　|　时间窗：2026-10-12 07:45–07:56 (+08:00)
> Key：`sk-personal-****`（全文脱敏）　|　脚本：`probe_models.py` / `probe_round2.py` / `probe_round3.py`
> 原始证据：`probe_results.json`、`probe_results_round2.json`、`probe_results_round3.json`（同目录）
> 交叉证据：W1 的实弹演练日志 `llm_pool.jsonl`（R5 / R7 的 503 样本来源）
> 结论一句话：**8 个模型全部可用，但 T3/T4/T5 是"先思考后回答"模型——`max_tokens` 给小了会返回 HTTP 200 且 `content` 为空**，这是本次探针最重要的发现，直接决定故障转移会不会误判降档。

---

## 一、结论速览（可用/不可用/风险项）

### ✅ 全部 8 个优先级链模型都可用（无 404 / 无不可用模型）

`/v1/models` 返回 12 个模型，用户优先级链的 8 个全部存在且全部实测 200：

| 档 | 模型 | 状态 |
|---|---|---|
| T1 | deepseek-flash / deepseek-flash-2 | ✅ 可用，**最快最稳** |
| T2 | deepseek-v4-flash-ascend / deepseek-v4-flash-ascend1 | ✅ 可用，表现与 T1 同级 |
| T3 | qwen3.8-flash-next-2 / qwen3.8-flash-next | ⚠️ 可用，但**思考型**（见风险 R1） |
| T4 | glm-5.3-flash | ⚠️ 可用，但**思考型 + 最慢 + 长输入逼近超时**（R1/R2） |
| T5 | qwen3.8-reasoner | ⚠️ 可用，`content` 正常非空（**不是**只返回 reasoning 的"哑巴"模型），但慢（R1） |

### 🔴 风险项（按严重度排序）

- **R1【高危·会直接导致误判降档】思考型模型在小 `max_tokens` 下返回 HTTP 200 但 `content` 为空。**
  T3 的两个、T4、T5 共 **4 个模型**属于"先输出 `reasoning_content`，再输出 `content`"的类型。
  当 `max_tokens` 不足以覆盖思考过程时，返回 `finish_reason="length"`，`content` 为 `null` 或 `""`，思考内容则在 `reasoning_content` 字段里。
  实测最低可用 `max_tokens`：`qwen3.8-flash-next-2` **64**、`qwen3.8-flash-next` **64**、`qwen3.8-reasoner` **64**、`glm-5.3-flash` **128**、`smart/reasoning` **64**。
  **若 `llm_pool.py` 里任何调用点用了 `max_tokens<=20`（例如意图识别、短分类），T3/T4/T5 会 100% 返回空内容 → 池会误判为"该模型故障"并降档，甚至在 T3 以下全空时误报"整个池挂了"。**
- **R2【高危·时延预算】40 字左右的小请求没问题，但长输入下 T4/T5 会吃掉 11–33 秒。**
  实测长输入（~3929 字，`max_tokens=1024~2048`）：`glm-5.3-flash` **14.9s / 15.1s**（mt=1024 时仍然空内容）、`qwen3.8-reasoner` **11.2s / 12.5s**、`smart/reasoning` **11.7s / 10.5s**、`qwen3.8-flash-next` 4.4–5.3s、`qwen3.8-flash-next-2` 4.7–8.0s。
  **`max_tokens=4096` 时 `glm-5.3-flash` 单次耗时 33.5 秒（实测 33535ms）** —— 这已经**超过设计里的 20s 单次尝试超时，并吃掉 45s 总预算的 74%**。
  设计里的**单次尝试 20s 超时对 glm-5.3-flash 余量极小甚至为负**；T4/T5 若超时，45s 总预算会被单次尝试吃掉一半以上。
- **R3【中危·JSON 陷阱】`response_format={"type":"json_object"}` 对 `qwen3.8-flash-next` 会"成功但空"**：HTTP 200、`finish_reason="stop"`，但 JSON 被塞进了 `reasoning_content`，`content` 是 `null`。**该模型这组参数必须禁用**（其它 7 个模型该参数正常，见 §2）。
- **R4【中危·400 不能一律当"模型不存在"】** 网关的 400 有两种完全不同的含义：
  - `model=""` → 400 `invalid_request_error` "Invalid model name passed in model=" → **等价于模型不存在**，可长冷却。
  - 参数非法（如 `temperature:"hot"`、`messages=[]`）→ 400 `litellm.BadRequestError` "Failed to deserialize / Validation" → **这是请求本身的问题，对任何模型都会 400**。若按设计"400 长冷却"，会把 8 个模型在一个请求里全部误冷却，等于自毁模型池。
- **R5【中危·日志脱敏 + 已实际发生】404 响应体会回显上游多 key 的内部标识**（`attempts` 数组里出现 `"key":"<上游key-1>"`、`"key":"<上游key-2>"`、`"key":"<上游key-3>"` 等**人名**），且可达 1KB 以上。
  **⚠️ 这不是假设——已核实 W1 的实弹演练日志 `llm_pool.jsonl` 第 4/7/8 行正在把整段 404 body（含这些名字）原样写进 `err` 字段。**
  → **请修正 `llm_pool.py`：`err` 字段只落 `error.type` + message 截断到 ≤200 字符。**
- **R6【中危·`/v1/models` 不是权威清单】网关能服务未列出的模型。**
  实测 `qwen-reasoner`（**不在** `/v1/models` 的 12 个里）→ **HTTP 200，`content='\n\n正常'`、`model` 字段回显 `qwen-reasoner`**，默认 `max_tokens=500` 下完全可用。
  **这直接影响 T5 的选型**：PM 的既定映射是"`qwen-reasoner` 网关上不存在 → 映射为 `qwen3.8-reasoner`"，但事实是 **`qwen-reasoner` 可用**，它就是"思考型 + content 非空"，行为与 `qwen3.8-reasoner` 同级（1.3–1.6s）。是否改回用户原始 T5 名称需 PM/用户定夺。
  （对照：`deepseek-v4-flash` 与 `deepseek-v4-pro` 实测 **404，确实不存在**，这两项映射按原样保留即可。）
- **R7【中危·同一故障两种错误码】"模型不可用"在网关上会以 404 或 503 两种形态出现。**
  我实测 `deepseek-v4-pro` **连续 4 次都是 404 `model_not_found`**；而 W1 在 07:49:52 的演练里请求同名模型拿到的是 **HTTP 503 `{"message":"所有 Key 均不可用","type":"no_keys_available"}`**。
  同一语义、同一模型名、不同时刻 → 不同状态码。**故障分类器不能只认 404。**

### ℹ️ 参考模型（供备选）

- **`smart/default` 是本次唯一的"全能选手"**：基础 chat 正常、JSON 正常、长输入仅 1.0s（比所有思考型都快）、`response_format` 正常，且**不是思考型**（`reasoning_content` 恒为 null）。若 T1/T2 全挂而 T3–T5 又受 R1 拖累，`smart/default` 是最理想的隐藏兜底（不在用户给定链里，**需 PM 决定是否加入**）。
- `qwen3.8-chat`：正常非思考型，但基础 chat 时延偏高（2.2–2.4s）。
- `smart/reasoning`：思考型，行为与 T3/T5 一致，无额外价值。
- `unlimited-ocr`：也能当 chat 用（HTTP 200），但定位是 OCR，不建议入链。

---

## 二、能力矩阵表

图例：✅ 正常　⚠️ 有坑　❌ 不可用　`—` 未测

| 模型 | 档 | 基础 chat | 时延中位(基础) | JSON 遵从 | 长输入(3929字) | `response_format` | 思考型 | 最低可用 max_tokens | 备注 |
|---|---|---|---|---|---|---|---|---|---|
| `deepseek-flash` | T1 | ✅ `正常` | **574ms** | ✅ `{"ok":true}` | ✅ 1.49s / 114字 | ✅ 支持 | 否 | 1 | **最快最稳，首选主力** |
| `deepseek-flash-2` | T1 | ✅ `正常` | 1101ms | ✅ | ✅ 3.32s / 96字 | ✅ 支持 | 否 | 1 | 稳定，慢 T1 兄弟约 2× |
| `deepseek-v4-flash-ascend` | T2 | ✅ `正常` | 837ms | ✅ | ✅ 3.75s / 129字 | ✅ 支持 | 否 | 1 | 稳，质量好 |
| `deepseek-v4-flash-ascend1` | T2 | ✅ `正常` | 1223ms | ✅ | ✅ 3.24s / 60字 | ✅ 支持 | 否 | 1 | 稳 |
| `qwen3.8-flash-next-2` | T3 | ⚠️ mt≤20 时**空**；mt≥64 ✅ | 918ms | ✅ | ⚠️ mt=1024 才 4.73s/66字 | ✅ 支持 | **是** | **64**（长文 **1024**） | 可用 `chat_template_kwargs` 关思考；mt=512 三次 1.0–1.8s 全稳 |
| `qwen3.8-flash-next` | T3 | ⚠️ mt≤20 时**空**；mt≥64 ✅ | 576ms | ✅ 带前导 `\n\n` | ⚠️ mt=1024 才 5.29s/110字 | ❌ **mt=300 时 content=null**（JSON 落进 reasoning_content） | **是** | **64**（长文 **1024**） | **禁用 response_format**；mt=512 三次 0.59–0.62s 最稳 |
| `glm-5.3-flash` | T4 | ⚠️ mt≤64 时**空**；mt≥128 ✅ | 698ms | ✅（但 4.7s，最慢） | ⚠️ mt=2048 才 15.1s/179字；**mt=4096 需 33.5s** | ✅ 支持 | **是** | **128**（长文 **2048+**） | **最慢**，20s 超时余量极小甚至为负 |
| `qwen3.8-reasoner` | T5 | ⚠️ mt≤20 时**空**；mt≥64 ✅ | 2013ms | ✅ | ⚠️ mt=1024 才 11.2s/201字 | ✅ 支持 | **是** | **64**（长文 **1024**） | `content` 非空 ✅（非哑巴模型）；mt=512 三次 1.3–1.5s 全稳 |
| `smart/default` | ref | ✅ `正常` | 2087ms* | ✅ | ✅ **1.04s / 92字** | ✅ 支持 | 否 | 1 | **全能备选**，长文最快 |
| `qwen3.8-chat` | ref | ✅ `正常` | 2318ms | ✅ | ✅ 2.24s / 125字 | ✅ 支持 | 否 | 1 | 正常，时延偏高 |
| `smart/reasoning` | ref | ⚠️ mt≤20 空；mt≥64 ✅ | ~2.0s | — | ⚠️ 11.7s | — | **是** | 64 | 无额外价值 |

\* `smart/default` 两次基础调用分别为 2989ms / 1184ms，波动大，中位仅 2 次样本，参考意义有限。

**关键读法**：T1/T2 四个模型（deepseek 系）是**纯 chat 模型**，任意 `max_tokens` 都稳定返回内容；
**T3/T4/T5 五个模型全是"思考型"**，`max_tokens` 是它们的生死线。

---

## 三、逐模型明细（含原始响应片段）

### 3.1 `deepseek-flash`（T1）—— 推荐主力

```
basic#1: HTTP=200 563ms  content='正常'
basic#2: HTTP=200 584ms  content='正常'
json:    HTTP=200 551ms  content='{"ok":true}'  -> json.loads OK
long:    HTTP=200 1491ms finish=stop clen=114
temp0.85/max500: HTTP=200 747ms clen=21
response_format: HTTP=200 677ms parsed={'ok': True}
usage:   {'completion_tokens': 2, 'prompt_tokens': 9, 'total_tokens': 11}
原始响应:
{"id":"chatcmpl-636181f6-...","created":1791762373,"model":"deepseek-flash","object":"chat.completion",
 "choices":[{"finish_reason":"stop","index":0,
   "message":{"content":"正常","role":"assistant","provider_specific_fields":{"refusal":null}},...}],
 "usage":{"completion_tokens":2,"prompt_tokens":9,"total_tokens":11,...}}
```
- 返回体里的 `model` 字段回显 == 请求的模型名，**可用于校验网关有没有偷偷换模型**。
- `max_tokens=999999` 也返回 200（被上游静默截断/忽略，不报错）。

### 3.2 `deepseek-flash-2`（T1）

```
basic#1: HTTP=200 1086ms content='正常'
basic#2: HTTP=200 1115ms content='正常'
json: HTTP=200 1046ms OK   long: HTTP=200 3320ms finish=stop clen=96
temp0.85/max500: HTTP=200 1307ms clen=21   rf: HTTP=200 833ms parsed OK
```
- 与 `deepseek-flash` 行为完全同构，**唯一区别是时延约 2 倍**（同档 T1 内适合排在 `deepseek-flash` 之后）。

### 3.3 `deepseek-v4-flash-ascend`（T2）

```
basic#1: HTTP=200 795ms content='正常'
basic#2: HTTP=200 879ms content='正常'
json: HTTP=200 901ms OK   long: HTTP=200 3751ms finish=stop clen=129
temp0.85/max500: HTTP=200 1057ms clen=21   rf: HTTP=200 833ms parsed OK
```
- 长输入回答质量最好（129 字，覆盖"硬编码 + 配置脱节"两点）。纯 chat 模型，无坑。

### 3.4 `deepseek-v4-flash-ascend1`（T2）

```
basic#1: HTTP=200 1224ms content='正常'
basic#2: HTTP=200 1221ms content='正常'
json: HTTP=200 1438ms OK   long: HTTP=200 3241ms finish=stop clen=60
temp0.85/max500: HTTP=200 3065ms clen=53   rf: HTTP=200 1556ms parsed OK
```
- `temperature=0.85` 时出现 3.0s 尖峰（其它模型同参数 <2s），建议留给池做时延观测。

### 3.5 `qwen3.8-flash-next-2`（T3）—— 思考型 #1

**⚠️ 核心坑：小 max_tokens 时空内容**

```
basic (max_tokens=20): HTTP=200 946ms content=''  finish=length  usage.completion_tokens_details={'reasoning_tokens':20,'text_tokens':0}
basic (max_tokens=20): HTTP=200 889ms content=''  finish=length
原始响应 (max_tokens=20):
{"id":"50a05cf0ae8c4601ac6a61583ea8361b","model":"qwen3.8-flash-next-2","object":"chat.completion",
 "choices":[{"finish_reason":"length","index":0,
   "message":{"content":"","role":"assistant",
              "reasoning_content":"用户说“只回复两个字：正常”，意思是要求我只回复两个字。\n用户可能希望我",...}}],
 "usage":{"completion_tokens":20,"prompt_tokens":57,"total_tokens":77,
          "completion_tokens_details":{"reasoning_tokens":20,"text_tokens":0}}}
```
- **`usage.completion_tokens_details.text_tokens == 0` 是"没产出正文"的确定性判据**，比看 `content` 是否为空更早、更可靠。
- `max_tokens` 扫描：`20 → 空`；`64/128/256/512/1024/2048 → content='\n\n正常'`（**注意有前导 `\n\n`，JSON 解析前必须 `.strip()`**）。
- 长输入：`mt=1024` → 4731ms / clen=66；`mt=2048` → 8011ms / clen=74。
- `response_format` ✅ 正常（1328ms，parsed ok）。

**关思考开关实测**：`chat_template_kwargs={"enable_thinking":false}` ✅ 637ms 干净返回 `正常`（`reasoning_content` 为 null）；`reasoning_effort="none"` ✅ 640ms；顶层 `enable_thinking:false` ❌ 被静默忽略。

### 3.6 `qwen3.8-flash-next`（T3）—— 思考型 #2

```
basic (max_tokens=20): HTTP=200 627ms content=None finish=length
原始响应:
{"id":"chatcmpl-FjJKA0R7y8ncAf7UtNrjPWrH-...","model":"qwen3.8-flash-next","system_fingerprint":"default",
 "choices":[{"finish_reason":"length","index":0,
   "message":{"role":"assistant","reasoning_content":"用户要求我只回复两个字：\"正常\"。这是一个简单的指令，我需要严格遵循。\n",
              "provider_specific_fields":{"refusal":null},"content":null},...}]}
```
**❌ 该模型的 `response_format` 是陷阱（R3）**：

```
response_format={"type":"json_object"}, max_tokens=200:
HTTP=200 finish_reason=stop  content=None  reasoning_content='{"ok":true}'   <-- JSON 落在 reasoning_content！
原始响应:
{"choices":[{"finish_reason":"stop","index":0,
  "message":{"role":"assistant","reasoning_content":"{\"ok\":true}",
             "provider_specific_fields":{"refusal":null},"content":null}}],
 "usage":{"completion_tokens":6,...}}
```
**注意这里是 `finish_reason=stop` 而不是 `length`**——"空 content" 不只出现在截断场景，**不能只用 `finish_reason=="length"` 判空**。

- 不带 `response_format` 时正常：`max_tokens=300` → `content='\n\n{"ok":true}'`（json.loads OK，但同样有前导 `\n\n`）。
- `max_tokens` 扫描：`20 → 空`；`64 及以上 → content='\n\n正常'`（稳定，600ms 上下）。
- 长输入：`mt=1024` → 5291ms/clen=110；`mt=2048` → 4369ms/clen=145。

### 3.7 `glm-5.3-flash`（T4）—— 思考型 #3，**最慢**

```
basic#1: HTTP=200 672ms content=None finish=length
basic#2: HTTP=200 724ms content=None finish=length
原始响应 (max_tokens=20):
{"model":"glm-5.3-flash","choices":[{"finish_reason":"length","index":0,
  "message":{"role":"assistant",
    "reasoning_content":"The user is asking me to reply with only two characters: \"正常\" (which means \"normal",...,
    "content":null},...}]}
```
- **`max_tokens` 门槛最高：64 仍然空（思考已经吃了 313 token），要到 128 才出 `正常`。**
- 长输入：`mt=1024` → **14966ms 且仍然空内容**（`finish_reason=length`）；`mt=2048` → **15108ms / clen=179**；**`mt=4096` → 33535ms / clen=122（思考 4804 字符）**。
  → **glm-5.3-flash 是整条链上唯一"抬 token 就线性涨到 30s+"的模型，20s 超时对它不成立。**
- `temperature=0.85, max_tokens=500`（短提示"用一句话说明什么是递归"）→ **7964ms 且 content=None**（思考吃了 953 token）；`max_tokens=2000` → 正常返回 55 字。**同一个模型在 T4 档对 token 预算极度敏感。**
- `response_format` ✅ 支持（555ms）。
- **`enable_thinking:false` 顶层参数会被网关拒绝：HTTP 400** `litellm.BadRequestError: ... Unsupported parameter(s)`。**绝不能给 glm 发这个参数。**
- 关思考：只有 `reasoning_effort="low"` 有效（481ms 返回 `正常`）；`chat_template_kwargs` 对它无效（被忽略）。
- 长输入 15s → **对"单次尝试 20s"的预算几乎顶格**。

### 3.8 `qwen3.8-reasoner`（T5）—— 思考型 #4，**content 非空（关键确认）**

**任务特别要求确认：`qwen3.8-reasoner` 的 `choices[0].message.content` 是否非空。结论：只要 `max_tokens≥64` 就非空，不是"只返回 reasoning_content 的哑巴模型"。**

```
basic (max_tokens=20):   HTTP=200 1388ms content=None finish=length   reasoning_content_len=39
basic (max_tokens=64):   HTTP=200 1460ms content='\n\n正常' finish=stop
basic (max_tokens=512):  HTTP=200 1480ms content='\n\n正常'
basic (max_tokens=2048): HTTP=200 3914ms content='\n\n正常'
json:  HTTP=200 1508ms content='\n\n{"ok":true}' -> json.loads OK
long:  mt=1024 -> HTTP=200 11183ms finish=stop clen=201
       mt=2048 -> HTTP=200 12520ms finish=stop clen=157
temp0.85/max500: HTTP=200 4938ms clen=47
response_format: HTTP=200 3352ms parsed={'ok': True}
```
- 原始空响应样本：
```json
{"model":"qwen3.8-reasoner","choices":[{"finish_reason":"length","index":0,
  "message":{"role":"assistant","reasoning_content":"用户要求我只回复两个字：\"正常\"。这是一个非常简单的指令，我只需要回复\"正常\"",
             "provider_specific_fields":{"refusal":null},"content":null},...}]}
```
- **机器人收不到回复的场景只有一个：`max_tokens` 给小了（≤20~32）。** 只要池统一把 `max_tokens` 抬到 ≥512，T5 完全可用于主回复。
- 关思考：`thinking={"type":"disabled"}` ✅ 999ms；`chat_template_kwargs={"enable_thinking":false}` ✅ 1453ms；顶层 `enable_thinking:false` → **HTTP 400**（同 glm，不可用）。

### 3.9 参考模型

**`smart/default`（建议纳入考虑）**
```
basic#1: HTTP=200 2989ms content='正常'
basic#2: HTTP=200 1184ms content='正常'
json: HTTP=200 643ms OK
long: HTTP=200 1039ms finish=stop clen=92     <-- 思考型里最快的长文，比 deepseek-flash 还快
temp0.85/max500: HTTP=200 666ms clen=47
response_format: HTTP=200 743ms parsed OK
reasoning_content: 恒为 null（非思考型）
```
- **唯一一个"非思考型 + 长文本快 + 全参数兼容"的 qwen/glm 系模型。**

**`qwen3.8-chat`**：basic 2193/2443ms，json 2576ms OK，long 2237ms/clen=125，rf OK —— 正常但慢。
**`smart/reasoning`**：basic `content=None`（mt=20）→ mt=64 `正常`；长文 11.7s。与 T5 同质，无独立价值。
**`unlimited-ocr`**：POST chat 也能 200（`"system_fingerprint":"vllm-0.22.1-dp2-..."`），定位 OCR，不入链。

### 3.10 `max_tokens=512` 重复稳定性（Round 3，每个模型 3 连发）

**结论：一旦 `max_tokens` 给够，4 个思考型模型 100% 稳定，无一次空内容。** 这进一步证明 R1 是纯参数问题。

| 模型 | #1 | #2 | #3 | 成功率 |
|---|---|---|---|---|
| `qwen3.8-flash-next-2` | 1221ms `'\n\n正常'` | 1032ms `'\n\n正常'` | 1826ms `'\n\n正常'` | 3/3 |
| `qwen3.8-flash-next` | 593ms `'\n\n正常'` | 620ms `'\n\n正常'` | 594ms `'\n\n正常'` | 3/3 |
| `glm-5.3-flash` | 1558ms `'正常'` | 1568ms `'正常'` | 2024ms `'正常'` | 3/3 |
| `qwen3.8-reasoner` | 1467ms `'\n\n正常'` | 1518ms `'\n\n正常'` | 1269ms `'\n\n正常'` | 3/3 |

- 小请求（20 字内）下时延排序：`qwen3.8-flash-next`(0.6s) < `qwen3.8-flash-next-2`(1.0–1.8s) ≈ `qwen3.8-reasoner`(1.3–1.5s) < `glm-5.3-flash`(1.6–2.0s)。
- `glm-5.3-flash` 的 `content` 是 `'正常'`（无前导换行），其余 3 个都是 `'\n\n正常'`。

---

## 四、错误形态样本（不存在模型 / 空 model 的完整响应）

这一节是 `llm_pool.py` 故障分类的**直接依据**。

### 4.1 模型不存在 → **HTTP 404 / `type: "model_not_found"`**

请求：`{"model":"does-not-exist-xyz","messages":[{"role":"user","content":"hi"}],"max_tokens":10}`

```
HTTP/1.1 404
```
```json
{"error":{"message":"没有 Key 支持模型 does-not-exist-xyz","type":"model_not_found",
 "attempts":[{"key":"<上游key-1>","kind":"capability","code":403,
   "message":"key not allowed to access model. This key can only access models=['unlimited-ocr', 'qwen3.8-flash-next-2', 'deepseek-flash', 'glm-5.3-flash', 'qwen-chat', 'deepseek-flash-2', 'qwen3.8-flash-next', 'sm...",
   "retry_after":null},
  {"key":"<上游key-2>","kind":"capability","code":403,"message":"key not allowed to access model. This key can only access models=['smart/reasoning', ...]"},
  {"key":"<上游key-3>","kind":"capability","code":403,"message":"key not allowed to access model. This key can only access models=['unlimited-ocr', ...]"}]}}
```

> 注：`attempts[].key` 的真实取值是**上游 key 持有者的人名**，本报告已脱敏为 `<上游key-N>`。这段 body 会在**每一次** 404 时回显（见 R5）。

**判读**：网关是个多上游 key 的路由器；"模型不存在"在这台网关上的真实语义是**"没有任何上游 key 有能力提供该模型"**。
→ 归入 **永久性错误，长冷却（600s）** 完全正确。
⚠️ 附带发现（R5）：body 会回显上游 key 的内部名字，且长度 >1KB。**W1 现有实现已把整段 body 写进 `llm_pool.jsonl`，需要截断。**

**同条件复现**：`deepseek-v4-pro`、`deepseek-v4-flash` 连续请求均为 404 `model_not_found`，时延 292–326ms，**判据稳定**。

### 4.2 `model=""` → **HTTP 400 / `type: "invalid_request_error"`**

```
HTTP/1.1 400
```
```json
{"error":{"message":"/chat/completions: Invalid model name passed in model=. Call `/v1/models` to view available models for your key.",
 "type":"invalid_request_error","param":null,"code":"400",
 "provider_specific_fields":{"error":"/chat/completions: Invalid model name passed in model=. Call `/v1/models` to view available models for your key."}}}
```
**判读**：与 404 语义等价（模型名无效），可同样长冷却。**但必须与下面 4.3 的 400 区分开。**

### 4.3 ⚠️ 其它 400（**参数错误，绝不能当"模型故障"**）

请求非法参数 `temperature="hot"`：
```
HTTP 400
{"error":{"message":"litellm.BadRequestError: OpenAIException - Failed to deserialize the JSON body into the target type: invalid type: string \"hot\", expected f32 at line 1 column 106No fallback model group found for lookup_groups=deeps..."}}
```
请求 `messages=[]`：
```
HTTP 400
{"error":{"message":"litellm.BadRequestError: OpenAIException - Validation: The 'messages' field cannot be empty. At least one message is required.No fallback model group found for lookup_groups=deeps..."}}
```
**判读**：这两类是**请求侧问题**，对 8 个模型会一模一样地 400。若池按"400 → 换下一个 + 长冷却"，一次坏请求就能把 8 个模型全部冷却掉，池当场瘫痪。

### 4.4 密钥无效（补充，供排障用）→ **HTTP 401**

```
HTTP 401
{"error":{"message":"invalid token","type":"auth_error"}}
```
**判读**：401 是**全局性**错误（所有模型都会 401），不应触发降档重试——应立刻中止并报警，否则 45s 预算会被 8 次无效重试烧光。

### 4.5 正向边界确认

| 用例 | 结果 |
|---|---|
| `max_tokens=999999`（deepseek-flash） | HTTP 200，上游静默接受（无 400） |
| `unlimited-ocr` 当 chat 用 | HTTP 200，正常返回 |

### 4.6 ⚠️ HTTP 503 `no_keys_available` —— 同一"模型不可用"的另一种形态（R7）

**样本来源**：W1 的实弹演练落盘日志 `llm_pool.jsonl` 第 9 行（`2026-10-12T07:49:52`，`label=drill-disable`，`model=deepseek-v4-pro`）：

```
HTTP 503
{"error":{"message":"所有 Key 均不可用","type":"no_keys_available",
 "attempts":[{"key":"<上游key-N>","kind":"capability","code":403,
   "message":"key not allowed to access model. This key can only access models=['smart..."}]}}
```

**对照**：我在 07:55 对**同一个模型名** `deepseek-v4-pro` 连续请求 4 次，**全部返回 404 `model_not_found`**（292–326ms）。

**判读（重要）**：
- 网关对"没有任何上游 key 能提供这个模型"这一种情况，**会随内部 key 健康状态在 404 与 503 之间摇摆**。
- 404 `model_not_found` → 明确是"模型不存在"，**长冷却 600s**。
- 503 `no_keys_available` → 语义上更接近"**所有上游 key 当前都不可用**"，**可能是一次全局故障（此时所有模型都会失败）**。
  → **若给 503 也上 600s 长冷却，一旦真的发生全局 503，8 个模型会全部被冷却 10 分钟，服务恢复后机器人还会继续哑 10 分钟。**
  → **建议 503 只给 60s 短冷却。**
- 两者共同点：**都不应触发"重试同一模型"**，都应换下一个。

### 4.7 ⚠️ `/v1/models` 不是权威模型清单（R6）

`qwen-reasoner` **不在** `/v1/models` 返回的 12 个模型里，但实测**完全可用**：

```
HTTP 200  1531ms  model_field='qwen-reasoner'  content='\n\n正常'  finish=stop
HTTP 200  1331ms  model_field='qwen-reasoner'  content='\n\n正常'  finish=stop
HTTP 200  1561ms  (max_tokens=200, JSON 提示)  content='\n\n{"ok":true}'  -> json.loads OK
reasoning_content: 非空（思考型），content 非空 ✅
```

**判读**：**不要用 `GET /v1/models` 的返回值去校验/裁剪优先级链**——`llm_pool.py` 若在启动时拿 `/v1/models` 做白名单过滤，会把可用的 `qwen-reasoner` 误剔除。
→ **建议：配置即真相，不做 models 清单校验；靠实际请求的 404/503 来发现模型不可用。**
（对照：`deepseek-v4-flash`、`deepseek-v4-pro` 实测 404，确实不可用。）

---

## 五、对故障转移实现的建议

### 5.1 各错误码 → 动作映射（**核心交付**）

| 网关返回 | 判据 | 建议动作 | 冷却 |
|---|---|---|---|
| 连接失败 / 读超时 | `httpx.ConnectError` / `TimeoutException` | 降档换下一个模型 | 60s |
| **HTTP 401** (`auth_error`) | `error.type=="auth_error"` | **立即中止整个请求并报警**（全局故障，重试无意义） | 不冷却，但打 ERROR 日志 |
| **HTTP 404** (`model_not_found`) | `error.type=="model_not_found"` | 降档换下一个模型 | **长冷却 600s** |
| **HTTP 400 + `invalid_request_error`** 且 message 含 `Invalid model name` | 见 §4.2 | 降档换下一个模型 | **长冷却 600s** |
| **HTTP 400 + `litellm.BadRequestError`**（参数/校验类） | 见 §4.3 | **立即中止，不换模型、不冷却**，把原始 message 抛给上层 | 不冷却 |
| HTTP 429 | （本次未触发，未取得样本） | 降档换下一个模型 | **长冷却 600s**（限流是配额问题，短冷却会打爆） |
| **HTTP 503 + `no_keys_available`** | 见 §4.6 | 降档换下一个模型 | **短冷却 60s**（可能是全局故障，长冷却会让恢复后继续哑 10 分钟） |
| HTTP 5xx（其它） | — | 降档换下一个模型 | 60s |
| **HTTP 200 但 `content` 空** | 见 5.2 | 降档换下一个模型 | **不冷却（这是参数问题不是模型问题，见 5.3）** |
| HTTP 200 但 JSON 解析失败 | `json.loads` 抛错 | 降档换下一个模型 | 60s |

### 5.2 "空内容"的判定 —— 请用两条判据而不是一条

```python
msg = body["choices"][0]["message"]
content = (msg.get("content") or "").strip()          # 注意：必须先 strip，模型会返回 '\n\n正常'
finish  = body["choices"][0].get("finish_reason")
detail  = (body.get("usage") or {}).get("completion_tokens_details") or {}

is_empty = (content == "")
# 判据一：reasoning-only（最可靠、最早可判）
think_only = detail.get("text_tokens") == 0 and (detail.get("reasoning_tokens") or 0) > 0
# 判据二：截断
truncated = finish == "length"
```
- **不要只用 `finish_reason == "length"`**：§3.6 里 `qwen3.8-flash-next` 在 `response_format` 下是 `finish_reason="stop"` 且 `content=None`。
- **也不要只用 `content` 判空**：`content` 可能是 `None`（首字即思考）或 `""`（思考后截断），两种都要覆盖。
- 建议**可选**地把 `reasoning_content` 兜底当作回复（仅在 `content` 空时启用），但这会把"模型的思考过程"发给 QQ 用户，**不推荐**。正确做法是 5.3。

### 5.3 最关键的实现建议：**给池加一个全局 `max_tokens` 下限，而不是把空内容当故障**

R1 的根因是调用点传了太小的 `max_tokens`。**在 `llm_pool.chat()` 内部统一做 `max_tokens = max(max_tokens or 0, 512)`（对 T3/T4/T5 生效；T1/T2 不受影响）**，即可一次性消除 4 个模型 100% 的"假故障"。

推荐档位（依据 §2 实测）：

| 场景 | 建议 `max_tokens` |
|---|---|
| 短分类/意图/JSON 判定 | **≥ 512**（思考型最低 64/128，留 4–8× 余量） |
| 正常主回复 | **≥ 1024**（思考型长输入实测需要 1024） |
| 长文本输入（>2000 字） | **≥ 2048**，且**预期 T4 会花 15s、T5 会花 11–12.5s**；若给 T4 开到 4096，实测要 **33.5s**（已超 20s 单次超时） |

**注意上限方向的风险**：抬高 `max_tokens` 会同时抬高时延（glm 长输入 1024→2048→4096 对应 14.9s→15.1s→33.5s）。
→ 建议 `max_tokens` 下限设为 **512**（覆盖所有思考型的最低需求），**长文本路径单独给 2048 并配合 5.4 的预算短路**。

**如果 PM 决定不抬高 `max_tokens`**，那么优先级链的**有效深度只有 T1+T2**：T3/T4/T5 在小 token 预算下会全部返回空内容被跳过（不算故障但等于不存在）。这本身也说得通——T1/T2 恰好是实测最快最稳的四个。

### 5.4 时延预算建议

- `deepseek-flash`/`ascend` T1/T2 基础 <1.3s，长输入 1.5–3.8s → **20s 单次尝试绰绰有余**。
- **`glm-5.3-flash` 长输入 15s（mt=2048）/ 33.5s（mt=4096）** → 20s 单次超时**余量极小甚至为负**，网络抖动就会误判降档。**这是全链唯一真正会撞超时的模型。**
- `qwen3.8-reasoner` 长输入 11–12.5s、`qwen3.8-flash-next` 4.4–5.3s、`qwen3.8-flash-next-2` 4.7–8.0s → 20s 内可完成，但 T5 余量也不宽裕。
- 注意与既有约束的耦合：设计里"单请求总预算 45s < decision_engine 60s 子进程超时"。**若 T4 用 30s、T5 再排队，45s 会被一个模型吃光**。建议总预算保持 45s 不变，并在 T4/T5 上做"剩余预算不足则直接跳过"的短路判断。
- **实操建议**：`llm_pool.chat()` 在发起 T4/T5 前检查 `剩余预算 < 预计耗时` 就直接跳过该档（短请求放行、长请求跳过 glm），避免把 45s 烧在最后一档上。

### 5.5 关思考参数（可选优化，**必须逐模型配置**）

实测无单一开关能通吃；且**发错参数会直接 400**（glm / qwen3.8-reasoner 拒绝顶层 `enable_thinking`）。

**单参数矩阵（`max_tokens=20`，只有 `content` 非空才算 WORKS）：**

| 模型 | `chat_template_kwargs={"enable_thinking":false}` | `reasoning_effort:"none"` | `reasoning_effort:"low"` | `thinking={"type":"disabled"}` | 顶层 `enable_thinking:false` |
|---|---|---|---|---|---|
| `qwen3.8-flash-next-2` | ✅ **WORKS 637ms** | ✅ WORKS 640ms | ✅ WORKS 830ms | ❌ 忽略 | ❌ 静默忽略 |
| `qwen3.8-flash-next` | ✅ WORKS 949ms* | ✅ WORKS* | ✅ WORKS* | （未测） | （未测） |
| `glm-5.3-flash` | ❌ 忽略 | ❌ 忽略 | ✅ **WORKS 481ms** | ❌ 忽略 | 🚫 **HTTP 400** |
| `qwen3.8-reasoner` | ✅ WORKS 1453ms | ✅ WORKS 1024ms | ❌ 忽略 | ✅ WORKS 999ms | 🚫 **HTTP 400** |

\* `qwen3.8-flash-next` 的单参数未单独测，但组合参数下 ✅（见下）。

**组合参数矩阵（Round 3）—— 发现 `chat_template_kwargs` 会"毒化" glm：**

| 组合 | next-2 | next | glm-5.3 | reasoner |
|---|---|---|---|---|
| `ctk{enable_thinking:false}` + `reasoning_effort:"none"` | ✅ 787ms | ✅ 949ms | ❌ **EMPTY**（rc=90） | ✅ 1155ms |
| `ctk{enable_thinking:false}` + `reasoning_effort:"low"` | ✅ 648ms | ✅ 422ms | ❌ **EMPTY**（rc=84） | ✅ 2837ms |
| `ctk` + `reasoning_effort:"none"` + `thinking:{type:disabled}` | ✅ 652ms | ✅ 472ms | ❌ **EMPTY**（rc=77） | ✅ 5111ms |

**关键结论**：`reasoning_effort="low"` **单独**发给 glm 有效（481ms），但一旦同时带上 `chat_template_kwargs`，glm 就退回思考模式并返回空内容。
→ **不存在一套通用的"关思考"参数；glm-5.3-flash 必须独占一套配置。**

**建议：第一版不要上关思考参数**（收益是省 token/降时延，风险是 400 和跨模型行为不一致）。用 5.3 的 `max_tokens` 下限解决 R1 更简单、更安全。若要做，只能做成 `config/model_pool.json` 里的 per-model `extra_params`，并且：
1. **永不发送顶层 `enable_thinking`**（glm / reasoner 直接 400）；
2. `glm-5.3-flash` 只能发 `reasoning_effort:"low"`，**不能带 `chat_template_kwargs`**；
3. `qwen3.8-flash-next-2` / `qwen3.8-flash-next` / `qwen3.8-reasoner` 用 `chat_template_kwargs` 或 `thinking`；
4. 关思考只是"提速"，**仍然要保留 5.3 的 max_tokens 下限做安全网**（关思考参数被忽略时是静默失败，不报错）。

### 5.6 其它给实现的提醒

1. **`content` 必须 `.strip()`**：T3/T5 稳定返回 `'\n\n正常'`、`'\n\n{"ok":true}'`。JSON 解析前不 strip 虽仍能 loads 成功，但直接回给 QQ 用户会带空行。
2. **不要依赖 `finish_reason=="stop"` 判断成功**（见 §3.6）。
3. **`response_format={"type":"json_object"}` 要按模型白名单放行**，`qwen3.8-flash-next` 必须排除（R3）。
4. **`llm_pool.jsonl` 的 `err` 字段截断到 ≤200 字符**，避免把 404 body 里的上游 key 人名写进日志（R5）——**W1 现有实现已在发生，需修**。
5. **建议记录 `usage.completion_tokens_details.reasoning_tokens`**：它是"这个模型是不是思考型、这次花了多少 token 思考"的唯一直接证据，对排查"为什么 T3 回复这么慢"极有价值。
6. **`smart/default` 建议纳入备选**（§3.9）：非思考型 + 长文 1.0s + 全参数兼容。是否加入优先级链需 PM 定夺——它不在用户原清单里。
7. **不要用 `/v1/models` 做白名单过滤**（R6/§4.7）：`qwen-reasoner` 不在清单里但可用。配置即真相。
8. **T5 选型待定**（R6）：用户原清单写的是 `qwen-reasoner`，PM 因"清单里没有"映射成了 `qwen3.8-reasoner`；但 **`qwen-reasoner` 实测可用**。两者都是思考型、content 非空、时延同级（1.3–1.6s vs 1.3–2.6s）。**建议 PM 向用户确认 T5 到底用哪个**；技术上两个都能用，保持现有映射也完全没问题（不需要为此返工）。

---

## 附：探针产物清单

| 文件 | 内容 |
|---|---|
| `PROBE.md` | 本报告 |
| `probe_models.py` / `probe_results.json` | Round 1：逐模型 6 项能力 + 错误形态 |
| `probe_round2.py` / `probe_results_round2.json` | Round 2：`max_tokens` 扫描 + 关思考开关矩阵 + 可疑样本复核 |
| `probe_round3.py` / `probe_results_round3.json` | Round 3：3×3 组合关思考参数矩阵（发现 ctk 毒化 glm）+ glm 长文 mt=4096 = 33.5s + 4 模型 mt=512 三连发稳定性 |

> 全部 key 在报告中脱敏为 `sk-personal-****`；原始响应片段已剔除任何完整密钥。
