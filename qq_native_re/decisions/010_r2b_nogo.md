# 决策日志 010 — R2b 判决：`NAPCAT_DISABLE_PIPE` **NO-GO**（达不到目标且有副作用）

> 日期：2026-10-13 06:40
> 决策者：PM
> 状态：**R2b 取消**；路线图第 2 步改判，直接推进 R3（NapCat 应用层升级 → 自愈）
> 依据：PM 亲读源码 + 二进制字符串取证（本轮 recon 子 agent 中途死亡，PM 接手完成）

## 一、R2b 的原目标

消掉命名管道 `\\.\pipe\NapCat_<pid>`（A2 取证：195 个管道中唯一带产品名的产物）。

## 二、决定性证据（互相独立的三条）

### 1) 管道是 **launcher 创建**的，不是 JS

`NapCatWinBootMain.exe`（32,768 B）内宽字符串块（偏移约 19,584–19,616）：
```
[NapCat Backend] Main Process ID:  |  \\.\pipe\NapCat_  |  Creating pipe:  |  Failed to create pipe:  |  Failed to create pipe connection thread:
```
⇒ 管道名是**硬编码格式**，**无改名配置**。

### 2) launcher **不认识任何** `NAPCAT_*` 环境变量

对该 exe 做宽/窄双编码正则搜索 `NAPCAT_[A-Z0-9_]*`：**命中 0 条**。
⇒ 它只读命令行与硬编码逻辑。

### 3) 该变量只门控 **JS 侧的客户端连接**

`napcat.mjs:116986`
```js
if (!(process.env["NAPCAT_DISABLE_PIPE"] === "1" || process.env["NAPCAT_WORKER_PROCESS"] === "1")) {
    await connectToNamedPipe(logger).catch((e) => logger.logError("命名管道连接失败", e));
}
```
（另 `napcat.mjs:117267` `isPipeDisabled: process.env["NAPCAT_DISABLE_PIPE"] === "1"` 仅用于状态上报；判定为**严格 `=== "1"`**）

## 三、判决与理由

| 项 | 结论 |
|---|---|
| 能否消掉产物 `\\.\pipe\NapCat_<pid>` | **不能** —— launcher 无条件创建，JS 客户端不是创建者 |
| 副作用 | launcher 仍将 QQ 的 stdout 灌入**无人读取**的管道；缓冲写满后**可能阻塞 QQ.exe**（stdout 背压） |
| 有无替代（改名而非关闭） | **无** —— 名称为 launcher 硬编码，且不得修改 NapCat 二进制（红线） |
| **判决** | **NO-GO**：达不到目标 + 引入新风险 |

## 四、路线图修订

- **R2b 删除**（本文即其墓碑，避免后人重走）。
- **下一步优先 R3**：NapCat **应用层**升级（**禁止整包**——OneKey 自 v4.18.30 起内嵌 QQ 9.9.33-52230，会毁掉 `versions\9.9.26-44498` 基线）。理由：10-13 06:23:55 出现**静默离线**（无 `[KickedOffLine]` 行），而 4.18.30+ 正是为“静默离线自愈”而加（等 60s → 自动重启 worker + 快速登录）；4.18.28+ 另有“被踢后 3.5s 自动重启” ⇒ **直接对症我们实测到的两种失效形态**。
- **并行 D1**：amgom 登录遥测常数随机化（Track F 已实证全用户同值 `"184","329"`，是目前唯一**确定服务端可见**的指纹）。
- B 类（环境变量/目录名卫生）：低成本 hygiene，**不改变服务端可见面**，优先级低于 R3/D1。

## 五、附：观察窗新样本（#7）

| # | 起点 | 存活 | 结束方式 |
|---|---|---|---|
| 7 | 10-12 21:03:11（R2a verbose 轮） | **9h20m44s** | **静默离线**（10-13 06:23:55，无踢点行） |

**诚实标注**：该样本同时变了**两个条件**（verbose 开启 + 夜间低活跃），且形态是静默离线而非踢——**不能归因**于任何单一变量。可作为后续假设（“被踢与账号活跃度/在线流量相关”）的线索，但需专门设计对照才能验证。

## 六、方法论备注

本轮 recon 子 agent 在完成前死亡，但留下关键线索（`connectToNamedPipe` 返回的 `disconnect` 无人调用），PM 据此接手并在**双编码（ASCII + UTF-16LE）**下重搜二进制才得出结论。教训：**搜 Windows 二进制字符串必须双编码兼顾**，否则会得到假阴性。
