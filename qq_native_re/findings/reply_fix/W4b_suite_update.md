# W4b — D6 验证套件陈旧预期与缺陷启发式更新

状态：**完成**（2026-10-12 13:12）
正向：`ALL PASS` / **exit=0**；反向：`--sabotage` **exit=0**（坏配置被抓）

## 0. 交付纪律

- 备份：`w3_d6_fix_check.py.bak-20261012-124619`（24,796B，SHA256 `C4AD690F…`，= 12:01 原文）
- **唯一改动的代码文件**：`qq_native_re/findings/reply_fix/w3_d6_fix_check.py`
  （24,796B → 37,013B）；新增产物：`_w4b_run_after.txt`、`_w4b_run_sabotage.txt`、
  `w3_d6_results.json`、`w3_d6_results_sabotage.json`
- 未动的 Runtime 文件：`config/decision_rules.json`（mtime 12:41:27）、
  `scripts/decision_engine.py`（mtime 11:58:16）——**均早于本次开工 12:56**；
  脚本内置只读断言另证运行前后 SHA256 不变（`5315CD05…` / `0A13FA29…`）

## 1. 起手判定与处置：**半更新且自相矛盾 → 先回滚再改**

判定依据（`read` 当前 26,237B/12:51:50 版本 + `Compare-Object` 与备份逐行 diff）：

| 部位 | 死者留下的状态 | 判定 |
|---|---|---|
| docstring | 已换成 W4b 版：自称有 `--sabotage`、"启发式已换成显式契约"、"W4b 后为 28 条" | 只是**承诺** |
| import | 多了 `copy` / `hashlib`（全文无引用） | 半成品 |
| CASES | L117 裸 `error`、L118 大写 `Error`、L139 `terror`、L140 `error bar` 仍写 `False` | **陈旧未改** |
| 启发式 | 仍是 `defect_present = any(c["valid"] …)` 计数推断 | **未改** |
| `--sabotage` | `grep sabotage\|argparse` 只命中 docstring 文本，无实现 | **不存在** |
| 收尾 | 仍 `if defect_present: sys.exit(2)` | 恒 exit=2 假警报 |

⇒ 正文一字未动、docstring 却宣称已改完，属"半更新且自相矛盾"。

**处置**：`Copy-Item .bak-20261012-124619 w3_d6_fix_check.py -Force` 覆盖回干净起点，
SHA256 与备份**逐位一致**（`C4AD690F5EF2FC809A1C7D0D3C3AC1C61B86096E69F9B96D4748C36ECE3BDF75`）、
AST OK，然后在该干净基线上改。

## 2. 背景（已定性）

`error_keywords` 由 20 条收窄为 **22 条**具体短语（新增 `an error occurred` /
`error occurred` / `traceback (most recent call last)`；`internal error` / `server error` 本就在列），
宽词 `error` 已移除。旧套件的 4 条预期写死了"宽 error"语义 ⇒ 与当前语义冲突；
旧"缺陷启发式"按"错误话术用例被判有效的条数"计数 ⇒ 收窄后恒有 **4** 条被判有效
⇒ 永远打印 `D6 缺陷存在` 且恒 exit=2（假警报）。

## 3. 改动清单

1. **4 条陈旧用例 → 应放行**（`expect_valid` False→True，归入 `放行` 组）
   - `错误话术: 纯 error` → 改名 `放行: 纯 error（宽词已移除）`
   - `错误话术: 句中 Error 大写` → 改名 `放行: 句中 Error 大写（宽词已移除）`
   - `误伤观测: terror 内含 error` / `误伤观测: 英文句子含 error 单词`（原名保留）
2. **新增显式用例**
   - 应拦截：`An error occurred while processing your request`、`Traceback (most recent call last):`（多行）
   - 应放行：`这个 error 挺奇怪`、`ArkUI 里有个 ErrorBar 组件挺好用`
3. **缺陷启发式**：删除计数推断，改为**显式语义契约**——
   每个用例带 `拦截/放行/边界` 分组，`defect_present = 拦截组漏放 or 放行组误伤`；
   另加 4 条**关键词覆盖证明**（证明拦截由 `internal error` / `server error` /
   `an error occurred` / `traceback (most recent call last)` 具体命中，不靠宽词 `error` 蒙对）
   + 1 条"宽词 `error` 已移除"断言。收尾不再无条件 `exit(2)`：`exit(0 if not FAILS else 1)`。
4. **新增 `--sabotage` 反向开关**：在**内存副本**上做坏配置（`copy.deepcopy(_content_checks)`
   后删除指定关键词），不写任何配置文件；跑 A/B 对照 + 逐短语消融。
5. **保留**：其余断言**一条未删**。

## 4. 复跑计数（真跑，原文见 `_w4b_run_after.txt`）

```
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w3_d6_fix_check.py
==== EXIT CODE = 0 ====
checks: PASS=34 FAIL=0
      应拦截组 24 例 → 判无效 24 例  ✓ 全拦
      应放行组 14 例 → 判有效 14 例  ✓ 全放
      既有边界 8 例（判定见上表，已逐例断言，改动前后一致）
  >>> 结论：**D6 已修复**（error_keywords 生效：应拦截全拦、应放行全放）
  [PASS] config/decision_rules.json: 运行前后 SHA256 不变（本套件只读，未改运行时文件）
  [PASS] scripts/decision_engine.py: 运行前后 SHA256 不变（本套件只读，未改运行时文件）
总体: ALL PASS
```

| | 改动前基线（12:49） | 改动后（13:12） |
|---|---|---|
| 结论行 | `D6 缺陷存在（改动前基线）` | `ALL PASS` |
| exit code | 2 | **0** |
| FAIL | 4（= 上表 4 条陈旧用例） | **0** |
| checks / cases | 67 / 42 | 80 / 46 |
| `defect_present` | true | **false** |

**"其余断言不变"的机检证据**：把改动前 `_w4b_d6_results_before.json` 与改动后
`w3_d6_results.json` 的非用例 check 名称列表做 unified diff ⇒ **只有 `+` 行、没有 `-` 行**：
原有 **25 条**（12 熔断 + 2 保真度 + 3 保真度复现 + 1 target + 7 配置健壮性）
**逐字同序保留**，新增 7 条契约/覆盖 + 2 条只读完整性 = 34 条。
（题述"22 项"为其子集，一条未动。）

## 5. 反向验证：坏配置必须翻红（原文见 `_w4b_run_sabotage.txt`）

```
& ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w3_d6_fix_check.py --sabotage
==== SABOTAGE EXIT CODE = 0 ====
  对比 A｜真配置（22 条）: 契约用例 38 例 → PASS（0 例偏差）
  对比 B｜坏配置（22 条，仅移除 'internal error'）: 契约用例 38 例 → FAIL 4 例
        [FAIL] 拦截｜错误话术: Internal error 前缀  → valid=True reason=None（期望 valid=False）
        [FAIL] 拦截｜错误话术: 小写 internal error  → valid=True reason=None（期望 valid=False）
        [FAIL] 拦截｜错误话术: 中文夹杂              → valid=True reason=None（期望 valid=False）
        [FAIL] 拦截｜错误话术: 全大写 INTERNAL ERROR → valid=True reason=None（期望 valid=False）
  [PASS] S-B 移除 'internal error' 后语义契约必须翻红（证明真会报警）   翻红 4 例
  逐短语消融（4/4 全 PASS）:
    消融 ['internal error']                        → Internal error: x 翻成放行 ✓
    消融 ['server error']                          → Server error 翻成放行 ✓
    消融 ['an error occurred','error occurred']    → An error occurred … 翻成放行 ✓
    消融 ['traceback (most recent call last)']     → Traceback (most recent call last): 翻成放行 ✓
  [PASS] S 只读完整性: config/decision_rules.json 未被本套件改写   sha256=5315cd05794f…
  [PASS] S 只读完整性: scripts/decision_engine.py 未被本套件改写   sha256=0a13fa29198a…
反向验证结论: **契约具备真报警能力**（真配置 PASS / 坏配置 FAIL 且点名到具体用例）
```

**两次对比**：同一套断言，真配置 **PASS（0 偏差）**；内存坏配置（仅删 1 条关键词）
**FAIL 4 例并被点名到具体用例** ⇒ 启发式不再是"恒真/恒假"，配置被改坏时**真会报警**。
`--sabotage` 退出码语义：`0` = 坏配置被抓住（预期行为）；`3` = 没抓住（套件是瞎的）。

## 6. 声明

- 本次只写 `qq_native_re/findings/reply_fix/w3_d6_fix_check.py` 及其产物文件；
  **未修改任何运行时代码与配置**（`decision_engine.py` / `decision_rules.json` 等）。
- `--sabotage` 的坏配置**只存在于内存**（`deepcopy` + 删键），从未落盘；运行前后
  SHA256 已逐条核对。
- **未重启 bot（PID 30376 未动）**，未碰 NapCat；重启由用户执行。
