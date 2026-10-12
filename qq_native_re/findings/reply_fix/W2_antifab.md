# W2：路 B 追加「禁止编造实时数值」约束 —— 完成报告

结论：**三项验收全部达成** —— 路 A 逐字不变（28+26 项断言全 PASS）；新组 **9/9** 回应「今日深圳天气」；编造率 **6/9 → 0/9**。两个模型池测试套件 exit=0。

- 备份（改前快照）：`qq_native_re/patches/reply_fix_backup_20261012-105922_antifab/`
  - `model_primary.py` SHA256 `78460753…34F0`、`model_fallback.py` SHA256 `19BD54FD…0BDF`
- 改后 SHA256：`model_primary.py` `73E1CFBC…1873`、`model_fallback.py` `B021DFD8…ED8`
- 全部证据文件：`qq_native_re/findings/reply_fix/`（见 §6）

## 1. 改动内容（仅路 B，两文件逐字相同）

| 文件 | 位置 | 行 |
|---|---|---|
| `scripts/model_primary.py` | `_build_prompt()` 路 B 末尾 | L94 后新增 L95–97 |
| `scripts/model_fallback.py` | `_build_prompt()` 路 B 末尾 | L99 后新增 L100–102 |

追加文本（逐字，前导 `\n` 作段落分隔）：

```
补充：若这条消息问的是实时/事实类信息（天气、新闻、比分、股价等），不要编造具体数值或细节（气温、涨跌幅、比分、日期都不要写具体数字）——可以说「本鱼查不到实时的」，然后自然地聊两句（比如「反正带伞总没错」）。
```

顺序：4 条注意项 → 针对本条作答 → **本约束** → `avoid_recent` 防复读段（仍在最后）。路 A 分支一个字符未动。

新路 B 全文（含 target 的实跑返回值，317 字）见 `W2_new_pathB_prompt.txt`：

```
【你需要回应的最新消息】UC: 今日深圳天气

---- 以下为群聊背景（仅供理解语境；不要去回答背景里其它未被回应的问题）----
[UC]: 摇一摇广告真烦
[芽1为真]: 后台可以关允许摇晃感应

注意：请只输出你要发送的纯文本回复内容，不要附带任何发送者昵称、不要使用 [昵称]: 的格式、不要包含 CQ 码。
请针对上面那条【你需要回应的最新消息】作答；若它在问具体事实而你不确定，就简短诚实地说明（不要编造）。
补充：若这条消息问的是实时/事实类信息（天气、新闻、比分、股价等），不要编造具体数值或细节（气温、涨跌幅、比分、日期都不要写具体数字）——可以说「本鱼查不到实时的」，然后自然地聊两句（比如「反正带伞总没错」）。
```

## 2. 路 A 逐字一致性 —— 全 PASS

`w1_prompt_baseline_check.py`（W1 既有脚本，未改动）**28/28 PASS，exit=0**：两文件的「无 target 与旧版逐字相同」「target=None+avoid_recent 非空仍走旧 prompt」「空 dict / content 全空白 / content 缺失回落旧 prompt」全 PASS，总体 `ALL PASS`。

另加 W2 专项 `w2_antifab_check.py`（**26/26 PASS，exit=0**），断言更强：

- 路 A 与 **pre-W2 备份**逐字相同，且与 **pre-W1 备份源码内联字面量**逐字相同（用 `ast` 从旧源码提取求值，**非手抄**）；
- 路 B：`新路 B == 旧路 B + 指定后缀`（无 avoid_recent）；`新路 B == 旧路 B + 指定后缀 + 原防复读段`（有 avoid_recent，顺序为先约束后防复读，防复读段逐字保留）；
- 两文件路 B 逐字一致（有/无 avoid_recent 两种）。

## 3. 回放新旧对比（真实故障现场数据，每臂 3 变体 × 3 次 = 9 次）

判定口径写进台架 `_replay.py`：① 是否回应 target（提到「天气」或「深圳」）；② 编造 = 命中 `\d+\s*(度|℃|°C|毫米|mm|%)` 或 `\d+\s*到\s*\d+` 或 `\d+\s*[~～\-—]\s*\d+`。fidelity 逐字断言：三变体 `fidelity_old=True fidelity_new=True`（生产函数返回值与台架模板逐字一致）。

| 组 | 回应 target | 编造（具体数值） |
|---|---|---|
| 旧 prompt（路 A，`old` 臂） | 3/9 | 0/9 |
| **新 prompt（路 B，`new` 臂）** | **9/9** | **0/9** |

与改动前对照（同一套正则跑存档 `replay_results_w1.json`，即 W1 旧路 B 的输出）：

| 版本 | 回应 target | 正则判编造 | 中文数字温度（正则外） | 合计 |
|---|---|---|---|---|
| W1 旧路 B（改前） | 9/9 | 4/9 | 2/9（「三十来度」「二十几到三十度」） | **6/9** ✅与 W1 人工判定 6/9 吻合 |
| **W2 新路 B（改后）** | **9/9** | **0/9** | **0/9** | **0/9** |

改后 9 条原文（全部明确说查不到实时 + 自然接话，无任何数字）：

1. A#1 `（尾巴一甩）本鱼哪知道深圳今天啥天，又没长天气雷达……反正带伞总没错，晒不晒的看命吧 🐋`
2. A#2 `（假装没听见前面那堆破事）本鱼查不到实时天气啦，我又没长气象雷达🫧 反正深圳这个季节，带伞总没错，顺便还能当遮阳用，晒成鱼干可不好吃`
3. A#3 `深圳今天？本鱼又没长天气雷达，查不到实时的。反正带把伞总没错，晒不着也淋不着。🫧`
4. B#1 `本鱼查不到实时的天气啦，脑袋里没联网那块儿。反正深圳这天气，出门带把伞总没错，晒的时候还能挡挡太阳。🫧`
5. B#2 `本鱼查不到实时的深圳天气啦，毕竟我这鱼缸没接气象台。反正出门带伞总没错，晴天遮阳雨天挡水，一举两得😏`
6. B#3 `深圳今天？本鱼查不到实时的啦，我又没长天气预报的尾巴。反正带伞总没错，深圳那雨说来就来，专挑你没带伞的时候下。🫧`
7. C#1 `深圳今天本鱼也查不到实时的，别指望一条鱼联网报天气。反正这个季节出门带伞总没错，晒成鱼干或者淋成落汤鱼都不划算🐋`
8. C#2 `本鱼又没长天气雷达，深圳今天啥天查不到实时的啦🐋 反正那地方不是暴晒就是突然泼水，出门带把伞总不亏。`
9. C#3 `深圳天气本鱼真查不到实时的，鱼脑袋不联网天气预报🫧 反正这季节出门带把伞总没错，你这吃白饭的别淋成落汤鱼`

**诚实标注（不影响结论，供 PM 判断）**：

- 旧臂本轮「回应 target 3/9」高于 W1 那轮的 0/9，是**采样波动**（旧臂 prompt 逐字未变，`fidelity_old=True`）；且这 3 条是**提到**该话题（含调侃「你复读三遍了」），并非真的作答，故旧臂失败现象依旧。
- 样本量 n=9、单一 target 问题（今日深圳天气）、单模型（deepseek-flash）、temperature=0.85；结论覆盖「天气类实时提问」，不能外推到比分/股价。
- 台架要求的目标区间是 0–1/9，实测 **0/9**，无需追加规则。

## 4. 测试套件（均 exit=0）

- `scripts/test_llm_pool.py` → `结果: 全部通过`，**exit=0**
- `scripts/test_pool_harden.py` → `结果: 全部通过`（H1–H6 全 PASS），**exit=0**
- `python -c "import reverse_bot; print('OK')"` → `OK`，**exit=0**（仅有的 warning 是 `logs/bot.2026-10-12.log` 被运行中的 bot PID 8272 占用，与本次改动无关）

## 5. 未动文件声明 + 一条运维提示

**只改了两个文件**（`model_primary.py`、`model_fallback.py`，均仅路 B）；另在 `qq_native_re/findings/reply_fix/` 下新建/更新了台架与分析脚本。**未动**：`llm_pool.py`（mtime 07:59）、`reverse_bot.py`（09:45）、`decision_engine.py`（仅只读查看）、任何 `config/` 与 persona（08:57）、`.env`（07:45）、NapCat、bot 进程（未重启）。红线文件 mtime 全部早于本轮开始时间 10:59。

运维提示（供 PM 决策，本次未据此做任何事）：`decision_engine.py:901` 用 `asyncio.create_subprocess_exec(python_exe, script_path)` **每条回复都新起一个进程从磁盘读脚本**，因此本次 prompt 改动**在下一轮回复即已生效、无需重启 bot**。若用户仍计划重启，对本改动无影响。

## 6. 本轮产生的证据文件

| 文件 | 说明 |
|---|---|
| `W2_antifab.md` | 本报告 |
| `replay_results.json` | 本轮回放（old/new 两臂 × 3 变体 × 3 次 + fidelity + summary/totals） |
| `replay_results_w1.json` | **改动前存档**（W1 那轮结果，原 `replay_results.json` 副本，未被覆盖） |
| `w2_antifab_check.py` | 路 A/路 B 结构断言 26 项（全 PASS） |
| `w2_antifab_analyze.py` | 用同一正则对改前/改后算编造率 |
| `W2_new_pathB_prompt.txt` | 新路 B prompt 全文 |

复跑命令：

```powershell
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_prompt_baseline_check.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w2_antifab_check.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\_replay.py
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w2_antifab_analyze.py
& ".\venv\Scripts\python" scripts\test_llm_pool.py
& ".\venv\Scripts\python" scripts\test_pool_harden.py
```
