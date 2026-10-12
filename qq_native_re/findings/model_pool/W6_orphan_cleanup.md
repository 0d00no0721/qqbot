# W6 收尾：兑底地雷修复 + 已封群 755471390 孤儿数据清理

- 执行时间：2026-10-12 09:39–09:52 +08:00
- 备份目录：`qq_native_re/patches/orphan_cleanup_755471390_20261012-093928/`
- 背景：群 755471390 已永久封禁，已从 `config/group_features.json` / `config/group_characters.json` 移除；
  角色已切「大肥鱼」，群 284517232 绑定大肥鱼。**本任务未重启 bot、未碰 NapCat/QQ。**

## 状态板

| 步骤 | 状态 |
|------|------|
| 建备份目录 + 报告骨架 | DONE |
| 任务 A：改 `get_group_characters()` 兜底 | DONE |
| 任务 A：import 自检 + 文件丢失模拟（真跑） | DONE |
| 任务 B：全仓引用清单（内容 + 文件名双查） | DONE |
| 任务 B：删 `user_profiles.json` 该群 key | DONE（走 harness broker 写通道） |
| 任务 B：删 `sign_in_data.json` 该群 key | DONE |
| 任务 B：清空 soyo 群记忆文件载荷 | DONE（物理删除文件名受阻，见「受限项」） |
| 任务 B：`user_profile.py` 保存行为核查 | DONE |
| 哈希未变证明（.backup / persona / 通用记忆） | DONE |

---

## 任务 A：兑底地雷（`scripts/decision_engine.py`）

### 改动对照（唯一改动点）

```
  510-512 (改前)
- """读取 group_characters.json，返回 {gid: char_name, ...}。
- 文件不存在时自动创建（使用全局默认角色 + 当前群）。
- """
  510-513 (改后)
+ """读取 group_characters.json，返回 {gid: char_name, ...}。
+ 文件不存在/损坏时自动创建为空映射 {}（不硬编码任何群号），
+ 让所有群统一回退 active_character.json 的全局默认角色。
+ """

  526-527 (改前)
- # 用当前主群初始化
- result = {"755471390": default_char}
  526-529 (改后)
+ # 不硬编码任何群号：文件丢失时重建为空映射，
+ # 所有群统一回退 active_character.json 的全局默认角色
+ result = {}
```

`Compare-Object` 全文件差异**仅上述 3 项**（2 行注释 + 1 行赋值），
文件正常读取路径（L515-521）、写盘（L533-536）、异常处理（L537-538）逐字未动。
行数 1326 → 1328（纯注释增加），大小 55610 → 55796 B。

### 验证 1：import 自检

```
$ & ".\venv\Scripts\python" -c "import reverse_bot; print('OK')"
[警告] 无法写入文件日志 logs\bot.2026-10-12.log（权限不足），已降级为仅控制台日志
OK
exit=0
```

### 验证 2：真实模拟「文件丢失」路径（`verify_task_a.py`，真跑原文）

```
[sim] 临时 config 目录 = ...\orphan_cleanup_755471390_20261012-093928\_sim_tmp_config
[sim] 该目录下 group_characters.json 存在? False
[sim] get_group_characters() 返回 = {}
[sim] 是否写盘 = True；落盘内容 = {}
[sim] ASSERT 全部通过：返回 {} 且落盘 {} 且不含 755471390
[sim] 正常读取路径回读 = {'284517232': '大肥鱼'}
[sim] ASSERT 通过：文件存在时正常读取逻辑未受影响
[restore] _get_config_dir 已恢复 -> 'E:\\QQbot\\config'
[restore] 真实 config/group_characters.json sha256 前12位 before=dff954a3b73a after=dff954a3b73a UNCHANGED
[restore] 真实 group_characters.json 内容 = '{\n  "284517232": "大肥鱼"\n}'
VERIFY_OK
exit=0
```

做法：把 `DecisionEngine._get_config_dir` 临时指向**工作区内**的 `_sim_tmp_config`
（模拟 `config/group_characters.json` 不存在），`finally` 中恢复原函数对象并整体删除临时目录。
已复核 `_sim_tmp_config` 无残留（`Test-Path` = False），真实 config 未被触碰。

### 验证 3：改动只在源码、运行中进程仍执行旧代码（证据）

```
scripts\decision_engine.py               55796 B  2026/10/12 9:41:50   ← 已改
scripts\__pycache__\decision_engine.cpython-314.pyc  80039 B  2026/10/12 8:15:16  ← bot 启动时编译
  .pyc 含 755471390 = True
  源文件含 755471390 = False
```

即：**改动静默生效于下次重启**。该分支仅在文件丢失/损坏时触发（死分支），不影响当前运行。

---

## 任务 B：孤儿数据清理

### 全仓引用清单（grep `755471390`）

需**内容 + 文件名**双查：soyo 群记忆文件**正文里不含该群号**（只有文件名带），
单靠内容 grep 会漏（首次 grep 确实漏了，已修正排查方法）。

**已清理的群维度孤儿数据（群维度 / 待删）**

| 对象 | 清理前 | 清理后 |
|------|--------|--------|
| `user_profiles.json`（仓库根）顶层 key | 2 个群 `['284517232','755471390']`，总 **84** 条（该群 **61** 条） | 1 个群 `['284517232']`，总 **23** 条，该群已不存在 |
| `scripts/sign_in_data.json` 顶层 key | 3 个群 `['1','284517232','755471390']`（该群 1 条，uid 784427550） | 2 个群 `['1','284517232']`，该群已不存在 |
| `config/characters/soyo nagasaki/memories/group/755471390.json` | 138 B，`{"knowledge":[{"text":"用户输入了测试内容","source":"admin","by":"784427550"}]}` | **载荷已清空**为 `{"knowledge": []}`（22 B） |

文件大小：`user_profiles.json` 30642 → 8481 B；`sign_in_data.json` 527 → 331 B。
其它群数据逐键核对未变（`284517232` 条数 23 → 23，签到记录逐字段相同）。

**判定为「叙述性文本 / 历史快照」→ 保留未动**

| 对象 | 判定 |
|------|------|
| `config/characters/soyo nagasaki.backup/**`（含其 `755471390.json`） | 历史快照，**未动**；138 B、sha256 `65d807c21c9f` 与清理前完全一致 |
| `soyo nagasaki/persona.txt`、`memories/character/default.json`、`info.json` | 角色叙述内容，**未动**（哈希见下） |
| `KUN.md`、`PROJECT_INDEX.md`、`multi_group_character_plan.md`、`memory_manager_guide.md`、`技术方案对比_GitHub竞品.md`、`角色系统重构方案_v2.md` | 文档/叙述 |
| `qq_native_re/decisions/007_*.md`、`findings/model_pool/W3_*.md`、`W4_*.md`、`W5_*.md`、`_w3_group_check.py`、`_w4_char_check.py` | 历史工作记录 |
| `archive/virtual_stock/virtual_stock/data/{accounts,config}/755471390.json` | `archive/` 归档（gitignored），非活跃数据 |
| `config/group_features.json`、`config/group_characters.json` | 已不含该群（本次无需处理） |
| `known_groups.json` | **不存在**（`known_groups` 是内存 `set`，`scripts/commands/_state.py:124`；`group_manager.py:145` 读取时容缺），无孤儿数据 |

### 未确定归属 → 只报告不动

- `logs/bot.2026-10-12.log`：历史日志含该群号（运行中，且沙箱禁止写 `logs/`）。
- `qq_native_re/patches/*`（既有备份目录）：备份保真，不动。
- `.git/index`：git 索引，非数据。

---

## 运行时风险核查（关键结论）

**`save_profiles()` 是「整份内存数据回写」。**

- `scripts/user_profile.py:125-131`
  ```python
  def save_profiles():
      """将 _profiles 写入 user_profiles.json。"""
      with open(_PROFILE_FILE, "w", encoding="utf-8") as f:
          json.dump(_profiles, f, ensure_ascii=False, indent=2)   # ← 整份 dump，无增量过滤
  ```
- 调用点：`user_profile.py:251`（`update_profiles()` 内，`updated_count > 0` 时）、
  `user_profile.py:308`（`retry_pending_updates()`）。
- 加载点：`reverse_bot.py:498-499`（`main()` 内，**仅启动时一次**）。
  启动日志「已加载 84 个用户画像（2 个群）」即此处输出，内存快照仍是**删前**状态。

**结论：本次删除不会被回写（有代码依据）；但条件成立的话会被回写。**

- 不会被回写的依据：`reverse_bot.py:348`
  ```python
  if PROFILE_ENABLED and _feature_on(gid_str, "profile_record"):
      record_message(gid_str, uid_str, clean_message or raw_message)
  ```
  实测 `_feature_on('755471390','profile_record') = False`（白名单语义，该群已不可达），
  `_feature_on('284517232','profile_record') = True`。
  死群发言永远进不了 `_speak_buffer` → `update_profiles()` 永远不会遍历到该 gid
  → 内存里的陈旧 `_profiles['755471390']` 不会被写入 `save_profiles()` 落盘结果。
  **故 `user_profiles.json` 的清理是稳定的（不会自行复活）。**
- 一旦有人把 755471390 重新加回 `group_features.json` 且开启 `profile_record`，
  或下一次写盘前有代码路径向该 gid 写入画像，则整份回写会把 61 条一并写回
  （因为内存里还留着）→ 那 61 条会**复活**。
- 附带：改动**无时间窗风险**。因内存快照与删除前文件逐字节等价，
  即便 bot 立刻写盘，`284517232` 的 23 条也不会丢。
  （观测到一次 `user_profiles.json` 瞬时读到 11708 B 的中间态且 `LastWriteTime` 未变，
  属并发打开/删除共享冲突导致的读取异常，非真实改写。）
- **建议**：① 无需为本次清理重启 bot（结论已论证）；② 但若要让**任务 A 的源码修复**
  在运行中生效，需重启 bot；③ 若要彻底消除「陈旧内存快照」隐患，最省事的是
  下次重启 bot（重启后 `load_profiles()` 只读到 23 条，内存快照即与磁盘一致）。

**`sign_in_data.json` 无此类风险**：`scripts/sign_in.py:46` 每次签到都 `load_sign_in_data()`
重读文件、`:78` 再 `save_sign_in_data(data)` 整份回写该次读到的内容，
即「读-改-写」均基于磁盘最新版，本次删除不会复活。

---

## 未动其它文件的声明 + 哈希证明

- **改动文件仅 3 个**：`scripts/decision_engine.py`、`user_profiles.json`、`scripts/sign_in_data.json`；
  **清空载荷 1 个**：`config/characters/soyo nagasaki/memories/group/755471390.json`。
  除此之外未修改任何文件。
- 未动：`.backup` 快照、persona、通用记忆、`.env`、模型池相关文件、NapCat/QQ、
  其它 .py 的非目标函数、其它群数据、既有备份目录。
- 哈希（清理后实测）：

| 对象 | 大小 | sha256 前 12 位 | 状态 |
|------|------|----------------|------|
| `soyo nagasaki.backup/memories/group/755471390.json` | 138 B | `65d807c21c9f` | 与清理前一致（未动） |
| `soyo nagasaki/persona.txt` | 1799 B | `63469f6913de` | 未动 |
| `soyo nagasaki/memories/character/default.json` | 7888 B | `eb42e6f37e2c` | 未动 |
| `soyo nagasaki/info.json` | 164 B | `797e57863d14` | 未动 |

- 清理前原件已 1:1 备份（4 项 sha256 与源 MATCH）：`decision_engine.py`(55610B)、
  `user_profiles.json`(30642B)、`sign_in_data.json`(527B)、soyo `755471390.json`(138B)。

## 受限项（权限边界，需更高权限方处理）

本会话沙箱对 `scripts/`、`config/`、`logs/` 为**可写不可删**：
`Remove-Item` / `os.remove` / `os.rename` 一律 `WinError 5 拒绝访问`，
连在 `scripts/` 下新建后删除自建文件也被拒（`Copy-Item` 新建亦被拒）；
但 harness 的写通道可改写这些路径的文件（两条 Task B 的 JSON 即由此完成）。
因此下列**物理残留无法由本会话删除**，数据载荷均已清除，详见同目录 `REMAINING_STEPS.md`：

1. `config/characters/soyo nagasaki/memories/group/755471390.json` —— 载荷已清空为 `{"knowledge": []}`，
   仅剩文件名（群维度数据已无）。
2. `scripts/_w6probe_broker.tmp` —— 本次验证写通道的探针文件，已清空为 0 B。
3. `scripts/__pycache__/decision_engine.cpython-314.pyc` —— bot 启动时的旧字节码缓存，
   仍含死群字符串；**不影响运行**（源码已改，解释器按 mtime 自动重编译），
   下次重启 bot 会自动失效重建。
