# 决策日志 011 — R3 执行成功：NapCat 应用层升级至 4.18.38（自愈已就位）

> 日期：2026-10-13 07:05
> 决策者：PM（用户拍板：4.18.38 + 影子暂存/受控替换/全备份）
> 状态：**R3 完成**；QQ 运行时基线**零改动**；bypass 仍 SUCCESS
> 依据：用户终端实录 + PM 读盘核实

## 一、验证清单（逐项实测）

| # | 判据 | 结果 |
|---|---|---|
| 1 | NapCat 版本 | ✅ 控制台 `[NapCat] [Core] NapCat.Core Version: **4.18.38**` |
| 2 | QQ 基线未动 | ✅ `versions\` 只有 `9.9.26-44498`（**无 9.9.33**）；`napcat.mjs` 已换为 3,130,951 B（旧 4,467,494 B） |
| 3 | 我们的 config 未被覆盖 | ✅ 运行配置 dump：`bypass` 六项**全 true** + `fileLog:true` + `autoTimeSync:true`；PM 读 `$NP\config\napcat.json` 亦为 6/6 true |
| 4 | **bypass 钩子在新版仍生效** | ✅ `napi2native_bypass.log` 07:00:52 新增 init 周期，`All bypasses result: **SUCCESS**`（累计 5 次） |
| 5 | OneBot 反向 WS | ✅ `:8080` LISTENING + **ESTABLISHED**（bot 28704 ↔ NapCat 46228） |
| 6 | 自愈代码已装入运行时 | ✅ `selfOfflineDelayMs`×1、`kickedDelayMs`×1、`SelfOffline`×3、`restartWorker`×1 |
| 7 | 群内功能正常 | ✅ 用户确认「群bot正常」；07:00:25 群 1055881417 收消息并正常回复 |

## 二、关键操作纪律（本次生效，值得固化）

1. **替换集硬排除 `config\`**（manifest 机检断言 0 命中）⇒ 从代码路径上杜绝“覆盖 config 导致 bypass 静默失效”（方案 §2.2 一号红线）。
2. **shell 根 launcher 一个字节未动**（只替换 app 目录内副本）⇒ 启动方式/扫码习惯不变。
3. `plugins\` / `logs\` / `cache\` 不在替换集。
4. 全备份 85.03 MB（含 `config/`、`native/`、启动链三件套），`revert_r3.bat` 一行还原（还原后 `napcat.mjs` 应回 `352154212C975F455E0D3925D3FC7E78468B0A2573B57F1EEDD672C9EB7F16A2`）。
   - ⚠️ **回滚警告**：备份含 `native\`，其中含 bypass 观测日志 ⇒ 回滚会把它退回快照版本。**回滚前先双击 `check_bypass_log.bat` 留档。**

## 三、附带观察

- 时间同步偏差：旧版 1500–2016 ms → 新版 **237 ms**。
- 新版多一行 `[Core] [Packet] [PacketClient] NativePacketClient Hook 初始化成功 platform=win32.x64`。
- 新群 1055881417 真实群名「碧油鸡乐土(二阶英灵殿）」，已回填配置。

## 四、对观察窗的影响（重要）

自愈一旦生效，**掉线后的行为会改变**（被踢 3.5 s 后自动重启 worker + 快速登录；静默离线等 60 s 后自愈）⇒ **新样本的“存活时长”含义与旧样本不可直接对比**。观察重点从“活多久”改为“掉线后是否自动恢复”。

## 五、下一步

- 观察 R3 效果：下一个掉线事件是否**自动恢复**（本路线核心判据）。
- 并行备选：**D1**（amgom 遥测常数）—— 需修改 NapCat 自身 JS，侵入性最大，待 R3 效果明朗后再定。
- 卫生项：`start_napcat_verbose.bat` 的中文行在 cmd（OEM 936）下被误解析（不影响功能），已改纯 ASCII。
