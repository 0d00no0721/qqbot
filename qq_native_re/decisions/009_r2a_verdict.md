# 决策日志 009 — R2a 判决：bypass 反检测套件【确已全面生效】

> 日期：2026-10-12 21:15
> 决策者：PM
> 状态：**R2a 达成目标** —— `decisions/008 §三.1` 的证据缺口补齐：bypass 不是“没跑”，而是“跑了也没用”
> 依据：`napi2native_bypass.log`（原生模块自写；93,984 B / 1,241 行；21:02–21:04 会话）；PM 亲读逐条核对

## 一、证据（原始日志）

| 判据 | 实测原文 | 结论 |
|---|---|---|
| 总开关 | `[BYPASS] ========== Enabling selected detection bypasses ==========` ×3 | 已启用 |
| **结果行** | **`[BYPASS] ========== All bypasses result: SUCCESS ==========` ×3** | **非 PARTIAL FAILURE** |
| IAT 防护 | `[BYPASS] IAT hook detection bypass enabled (11 DLLs protected)`；`[BYPASS-IAT]` 共 **1080 行**（`Spoofing AllocationBase`、`VirtualQuery triggered from wrapper`） | 正在实时伪造官方 `wrapper.node` 的查询 |
| 模块枚举隐藏 | `[BYPASS-MODNUM] K32EnumProcessModules: hidden 12 modules (total 83 -> 71)`；`GetModuleHandleW: hiding frida-agent.dll` | 生效 |
| 进程/路径伪装 | `[BYPASS-PROCESS] GetModuleFileNameW bypassed: E:\QQbot\NapCat\NapCat.44498.Shell\QQ.exe` ×50 | 生效 |
| JS 列表伪装 | `[BYPASS-WIN] Init JS List Bypass`；`NodeContextifyContextMetrics1 bypass triggered. Fake count: 7` | 生效 |
| Hook 就位 | `[HOOK_INIT] Initializing Frida Gum`；`send hook with send_rva=0xa1051c`；`wrapper.node base_address=0xc65c0000` | 生效 |
| **失败/警告** | **0 行**（无 `wrapper.node not loaded for IAT bypass`、无 `Failed to hook`、无 `PARTIAL`） | 无中间态失败 |

## 二、判决

1. bypass 六项及 IAT/JS 子机制**全部介入且无警告** ⇒ 第一轮观察窗失败**不能**归因于“bypass 未生效”（008 §三.1 缺口补齐）。
2. 与 Track F §2.2 一手案例 #2062（反检测全开仍 ≤1h 被踢）**同构** ⇒ bypass 对检测型踢下线**无效**。
3. 路线图重注转向 bypass **覆盖不到**的面：B1 命名管道名 / B2 环境变量 / B3 磁盘目录名 / **D1 amgom 登录遥测常数（服务端可见）** / C1-C2 版本因子 / D2 协议层。
4. **副产品：测量仪器** —— 此后任何配置变更都可对比本日志确认“是否真的生效”。

## 三、两处运维修正（本次实测发现，已派单修复）

1. **日志真实落盘位置**：`E:\QQbot\NapCat\NapCat.44498.Shell\versions\9.9.26-44498\resources\app\napcat\native\napi2native\napi2native_bypass.log`（**原生模块自身目录**）。手册原先据“裸文件名 + CWD”推断为安装根目录——**推断错误**；`check_bypass_log.ps1` 的四个候选位置**全部漏掉**（该路径深 7 层，超出 `-Depth 3`）。
2. **该日志无轮转保护**：会话开始 2 分钟内即 94 KB ⇒ 已立即摘录留档至 `findings/rounds/`。

## 四、观察窗样本（截至 21:15，共 6 例 + R2a 轮）

| # | 起点 | 存活 | 结束方式 |
|---|---|---|---|
| 1 | 07:28:31 | 2h06m43s | 踢（泛化行） |
| 2 | 09:43:02 | 55m24s | 踢（泛化行） |
| 3 | 11:06:56 | 51m47s | **静默离线**（无通知） |
| 4 | 12:12:35 | 45m | 用户手动重启（非踢） |
| 5 | 12:57:40 | 3h15m57s | 踢（泛化行） |
| 6 | 18:03:45 | 2h41m12s | 踢（泛化行） |
| 7 | **21:03**（R2a 轮） | 进行中 | verbose 会话；本轮意义：即使再被踢，也能确认 bypass 确实在跑 |

## 五、下一步

- **R2b**：`NAPCAT_DISABLE_PIPE=1`（零成本；且现在可用 bypass 日志验证变更是否生效）+ 观察被踢间隔是否改变。
- 并行推进 **D1**（amgom 遥测常数随机化 —— 服务端可见指纹，Track F 已实证全用户同值 `"184","329"`）。
- 纪律不变：每轮记 t0 与被踢原因行；不混变量。

---

## 附：留档快照与记录员核对（PM 记录员补记，2026-10-12 21:15）

**唯一证据已原样留档**（该日志无轮转保护，持续增长中）：

| 项 | 值 |
|---|---|
| 留档副本 | `findings/rounds/R2a_bypass_log_20261012-2112.txt` |
| 副本字节 / 行数 | **120,528 B / 1,553 行** |
| 副本 SHA256 | `E698556CAE233A7F4AE4039FE1D5EE08985465AACAA8F1D067A347484C262D4D` |
| 源文件 SHA256（同刻读取） | `E698556CAE233A7F4AE4039FE1D5EE08985465AACAA8F1D067A347484C262D4D`（**与副本逐字节一致**） |
| 摘录时刻 | 2026-10-12 21:15:17（本地）/ 13:15:17Z |
| 源文件 | `...\resources\app\napcat\native\napi2native\napi2native_bypass.log`（**只读**，NapCat 目录内零写入） |

**行数说明**：正文引用 PM 首读时点（93,984 B / 1,241 行）；留档时点已增长至 120,528 B / 1,553 行——恰好印证 §三.2“无轮转保护”。PM 表内各项判据在留档副本中**逐条复核成立**：`All bypasses result: SUCCESS` ×3；失败/警告（`PARTIAL` / `Failed to hook` / `not loaded for IAT bypass` / `ERROR` / `WARN`）**均为 0 行**；`hidden 12 modules` ×3；`Fake count: 7` ×4；`send_rva=0xa1051c` ×4。

**一处计数口径澄清**：`GetModuleFileName*` 共 **50** 行 = `GetModuleFileNameW` **35** + `GetModuleFileNameA` **15**（PM 表中写的 ×50 是合计口径，成立）。`[BYPASS-IAT]` 在留档时点为 **1,392 行**（首读时点 1,080 行），增速与 IAT 实时伪造语义一致。
