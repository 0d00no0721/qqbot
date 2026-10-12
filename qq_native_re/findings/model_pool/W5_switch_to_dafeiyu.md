# W5 清理已封禁群 755471390 + 启用「大肥鱼」角色

状态：**完成并验证通过**
执行窗口：2026-10-12 09:31:18 → 09:32:34 (+08:00)
执行者：实现子 agent（W5）｜项目根 E:\QQbot

---

## 0. 结论速览

| # | 验证项 | 结果 |
|---|--------|------|
| 1 | 角色解析 284517232 | **大肥鱼** ✅ |
| 2 | 全局 enabled / character | **True / 大肥鱼** ✅ |
| 3 | 别名表 = 大肥鱼 5 触发词、值全为 284517232 | **✅（恰 5 条）** |
| 4 | soyo 别名（素世/soyo/soyorin/naga/saki…）退出触发表 | **✅ 残留 0 条** |
| 5 | 角色列表未删任何角色 | **✅ 仍含 soyo nagasaki 与 .backup** |
| 6 | `_feature_on` 三群真实断言 | **✅ 284517232 五项全 T／1055881417 五项全 F／755471390 五项全 F** |
| 7 | 三个 json `json.load` | **✅ 全部通过** |
| 8 | 数据未动（记忆/画像 SHA256） | **✅ 与基线逐字一致** |
| 9 | 文件系统级证据 | **✅ config/ 递归仅这 3 个 json mtime 变化** |
| 10 | bot 未重启 | **✅ PID 8272 存活，StartTime 08:15:16，:8080 LISTENING** |

---

## 1. 备份（第一动作）

`E:\QQbot\qq_native_re\patches\character_switch_backup_20261012-093118\`

| 文件 | 字节 | 备份时 SHA256 |
|------|------|---------------|
| group_features.json | 693 | `11553B44E76657BE577DD99F20F10BB886C159F8A159254516E37E2814CB1B70` |
| group_characters.json | 66 | `EB2EE3E0DBBAFED4380296A23E9FBD3636E01D7B448DF2EEFAAD044DDAE01D66` |
| active_character.json | 97 | `B71F02F650D0F9CEDCF6E5302647FA6A99600782E959899221AFEE8BF1CB241A` |

---

## 2. 改动内容（三个文件）

### 2.1 `config/group_features.json`（693 → 475 B）
- **删除**整个 `"755471390"` 条目（含 features 块）+ 其前导逗号。
- `284517232`（五项全 true、group_name「碧油鸡乐土（转正版）」）与 `1055881417`（五项全 false）**逐字未动**。

改后全文：
```json
{
  "284517232": {
    "group_name": "碧油鸡乐土（转正版）",
    "features": {
      "decision_engine": true,
      "plus_one": true,
      "commands": true,
      "newcomer_welcome": true,
      "profile_record": true
    }
  },
  "1055881417": {
    "group_name": "",
    "features": {
      "decision_engine": false,
      "plus_one": false,
      "commands": false,
      "newcomer_welcome": false,
      "profile_record": false
    }
  }
}
```

### 2.2 `config/group_characters.json`（66 → 31 B）
改前 → 改后：删除 755471390；284517232 由 `soyo nagasaki` 改为 `大肥鱼`。
```json
{
  "284517232": "大肥鱼"
}
```

### 2.3 `config/active_character.json`（97 → 93 B）
仅 `character` 字段 `soyo nagasaki` → `大肥鱼`；`enabled` 保持 `true`，`admin_ids` 逐字不动。
```json
{
  "enabled": true,
  "character": "大肥鱼",
  "admin_ids": [
    "784427550"
  ]
}
```

改后 SHA256：

| 文件 | 改后 SHA256 |
|------|-------------|
| group_features.json | `31201BEDF5FAFFC4BD43938E5313BA35270353E7A7823516059040C1D5A94B48` |
| group_characters.json | `DFF954A3B73A39372A4BFD3D244F7ED64B41EE42670B7E072D96E4DE876A8202` |
| active_character.json | `985C2298148121274686CE3AA1A6322797355A875DA94BBAE617EC9A62A0A4C8` |

---

## 3. 验证（真跑原文）

复跑脚本：`qq_native_re/findings/model_pool/_w5_switch_check.py`（只读）
```
cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; .\venv\Scripts\python qq_native_re\findings\model_pool\_w5_switch_check.py
```

### 3.1 改动前（基线，证明 delta 真实存在）
```
角色解析 284517232 = soyo nagasaki
全局 enabled= True character= soyo nagasaki
别名表 = {'素世': '755471390', 'soyo': '755471390', 'soyorin': '755471390', 'naga': '755471390', 'saki': '755471390', 'saki酱': '755471390', '长崎素世': '755471390', 'soyo酱': '755471390', '素世酱': '755471390'}
角色列表 = ['soyo nagasaki', 'soyo nagasaki.backup', '大肥鱼', '贴吧鱼']
群绑定 = {'755471390': 'soyo nagasaki', '284517232': 'soyo nagasaki'}
```
> 注意改前别名**全部指向已封禁群 755471390**——这正是本次修复的实际缺陷。

### 3.2 改动后（post-change，同一脚本）
```
角色解析 284517232 = 大肥鱼
全局 enabled= True character= 大肥鱼
别名表 = {'大肥鱼': '284517232', '肥鱼': '284517232', '小鲸鱼': '284517232', '鲸鱼娘': '284517232', 'deepseek娘': '284517232'}
角色列表 = ['soyo nagasaki', 'soyo nagasaki.backup', '大肥鱼', '贴吧鱼']
群绑定 = {'284517232': '大肥鱼'}
```

### 3.3 别名表严格断言
```
别名表 keys        = ['大肥鱼', '肥鱼', '小鲸鱼', '鲸鱼娘', 'deepseek娘']
期望(5, 引擎小写化) = ['deepseek娘', '大肥鱼', '小鲸鱼', '肥鱼', '鲸鱼娘']
集合完全相等       : True | 数量 = 5
所有值 == 284517232: True
soyo 残留别名      : [] => 已退出触发表: True

大肥鱼 info.json   = {'name': '大肥鱼', 'aliases': ['肥鱼', '小鲸鱼', '鲸鱼娘', 'DeepSeek娘'], 'description': 'DeepSeek V4 二创娘化形象：活泼俏皮、贱兮兮但心软的蓝色鲸鱼娘；纯本体人设，不带贴吧语体'}
```
> 说明：引擎在 `decision_engine.py:710/722` 对别名做 `.lower()`，故表中第 5 个键显示为 `deepseek娘`（源 `info.json` 写的是 `DeepSeek娘`）。匹配侧 `reverse_bot.py:295-297` 同样先 `msg_lower = clean_or_raw.lower()` 再做子串包含判断，**两侧同时小写化，实际触发大小写不敏感、语义与「DeepSeek娘」完全等价**，非缺陷。

### 3.4 `_feature_on` 真实断言（import 真 reverse_bot）
```
_feature_on from E:\QQbot\reverse_bot.py
284517232 {'decision_engine': True, 'plus_one': True, 'commands': True, 'newcomer_welcome': True, 'profile_record': True}
1055881417 {'decision_engine': False, 'plus_one': False, 'commands': False, 'newcomer_welcome': False, 'profile_record': False}
755471390 {'decision_engine': False, 'plus_one': False, 'commands': False, 'newcomer_welcome': False, 'profile_record': False}

284517232 五项全 True : True
1055881417 五项全 False: True
755471390 五项全 False (已删除/白名单语义): True
```

### 3.5 `json.load` 三文件
```
JSON OK: config/group_features.json -> ['284517232', '1055881417']
JSON OK: config/group_characters.json -> ['284517232']
JSON OK: config/active_character.json -> ['enabled', 'character', 'admin_ids']
```

### 3.6 沙箱内预期噪音
```
[警告] 无法写入文件日志 logs\bot.2026-10-12.log（权限不足），已降级为仅控制台日志
```
属沙箱内 `logs\` 不可写的预期降级，不影响任何断言。

---

## 4. 数据未动证明（SHA256 + mtime，改前/改后逐字一致）

### `config/characters/soyo nagasaki/**`
| mtime | SHA256 | 文件 |
|-------|--------|------|
| 2026-06-27 19:43:54 | `797E57863D14225680E5157518779C5EC194CF0CAB1D38F67E063D3E9CFB3924` | info.json |
| 2026-07-12 21:47:01 | `EB42E6F37E2CD04B62E6833B89D3099D1432B88D0C74A995F7EC74D49F2AB243` | memories\character\default.json |
| 2026-07-05 11:06:26 | `65D807C21C9F7CE5B67A245C7B032B34C5D190E483CB64AA2E3834E4F03FCD37` | memories\group\755471390.json |
| 2026-07-12 21:47:58 | `63469F6913DEB31E145C97C69ECCEAC47D19D9D3AD5D85F2DEE951ED62B0C606` | persona.txt（6181→未改） |

### `config/characters/soyo nagasaki.backup/**`
| mtime | SHA256 | 文件 |
|-------|--------|------|
| 2026-06-27 19:43:54 | `797E57863D14225680E5157518779C5EC194CF0CAB1D38F67E063D3E9CFB3924` | info.json |
| 2026-07-05 10:55:56 | `0965891C6F568D99BB289DCBA1DD1D8F16CF6BA8CCD00E48BBF48305CE86BB18` | memories\character\default.json |
| 2026-07-05 11:06:26 | `65D807C21C9F7CE5B67A245C7B032B34C5D190E483CB64AA2E3834E4F03FCD37` | memories\group\755471390.json |
| 2026-06-27 16:37:40 | `9CA9A834EC4F363E09D23D408DFC32FAA97919F5E35164080F9C5C787BFFDEC1` | persona.txt |

### `user_profiles.json`（位于**仓库根**，非 config/ 下）
- mtime `2026-07-20 09:41:53`，30642 B，SHA256 `52425A16BF4C82F09A815784017FC52293EC2AC4E53ABBFA43B0B2B28F9F9623`
- 改前改后完全一致（本任务全程未写）。

> **保留说明**：`soyo nagasaki/memories/group/755471390.json` 与 `soyo nagasaki.backup/memories/group/755471390.json`、以及 `user_profiles.json` 中残留的群 755471390 引用**按红线一律保留、未删**——用户只要求清理配置里的群，未要求删记忆/画像，且保留完全可逆。

---

## 5. 文件系统级证据

**本任务窗口（≥ 2026-10-12 09:30）内 `config/` 递归 mtime 更新过的文件，恰为这三个 json：**
```
2026-10-12 09:32:34	config\active_character.json
2026-10-12 09:32:34	config\group_characters.json
2026-10-12 09:32:34	config\group_features.json
```
对照：`config/` 全量文件 mtime 中，其余全部早于窗口（model_pool.json 08:08:16、decision_rules.json 08:08:14、active_model.json 07:58:10、persona.txt 2026-07-12、labels.json 2026-06-15）。

**bot 未重启**：
```
Id: 8272  ProcessName: python  StartTime: 2026/10/12 8:15:16
TCP  0.0.0.0:8080  0.0.0.0:0  LISTENING  8272
```
`reverse_bot.py` 三处配置均为每次调用即读（热加载），**下一条群消息即生效，无需重启**。

---

## 6. 未动其它文件声明

- **未改任何 `.py`**（含 `decision_engine.py`、`reverse_bot.py`、`scripts/**`）。
- **未改任何角色目录内文件**（persona / info.json / memories 全部原样）。
- **未改 `user_profiles.json`**、未改 `scripts/sign_in_data.json`。
- **未碰 NapCat / QQ**，未重启 bot（PID 8272 自 08:15:16 起持续运行）。
- 新增文件仅 2 个（均在 `qq_native_re/` 下）：本报告 + 只读校验脚本 `_w5_switch_check.py`。
- 新增目录仅备份目录 `qq_native_re/patches/character_switch_backup_20261012-093118/`。

---

## 7. ⚠️ 遗留观察（未处理，供 PM 决策）

`scripts/decision_engine.py:522-527`（`get_group_characters` 的**初始化兜底分支**）硬编码：
```python
# 文件不存在或损坏 → 从全局默认初始化
result = {"755471390": default_char}
```
该分支仅在 `config/group_characters.json` **不存在或 JSON 损坏**时触发（`FileNotFoundError` / `JSONDecodeError`）。当前文件存在且合法，**此路径为死代码，不影响运行**。
但它是本次任务的**潜在回退风险**：一旦该 json 丢失/损坏，引擎会自动重建出已封禁群 `755471390` 的绑定，而唯一在用的 `284517232` 反而无绑定（此时别名表会退化为"全局默认追加"语义）。
按红线（不改任何 .py）**本次未改动**，建议后续单独排期改为 `{"284517232": default_char}` 或动态取当前主群。
