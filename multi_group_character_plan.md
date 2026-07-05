# 多群多角色改造方案

> 创建时间：2026-07-05
> 基于版本：2.2.1

---

## 一、当前架构分析

### 数据流

```
用户发消息
  → reverse_bot.py (别名检测, 仅读取当前活跃角色)
  → decision_engine.should_reply()
  → _call_model_script()
  → _load_character(gid)  ← 关键节点
       ├─ active_character.json     (全局开关 + 角色名)
       ├─ characters/{char}/persona.txt
       ├─ characters/{char}/memories.json
       └─ user_profiles.json[gid]   (唯一用到 gid 的地方)
  → 拼接成 system_prompt → 子进程 → LLM
```

### 当前状态

| 维度 | 现状 |
|------|------|
| **角色作用域** | **全局单一**：一个 `active_character.json` 控制所有群 |
| **记忆作用域** | **绑定角色**：每个角色一份 `memories.json`，不区分群 |
| **别名检测** | **全局单一**：只读取当前活跃角色的 `info.json` 别名 |
| **gid 参数** | `_load_character(gid)` 接收了 gid，但仅用于加载群友画像，**未用于角色选择** |
| **管理员** | 全局 `admin_ids` 列表，不区分群 |
| **上下文缓冲区** | 已按群隔离（环形缓冲区 key 含 gid） ✓ |

### 存在的问题

1. **角色全局共享**：所有群用同一个角色，无法差异化
2. **记忆不区分群**：同一角色在不同群的记忆混在一起
3. **别名全局**：只识别当前活跃角色的别名，其他角色名字被忽略
4. **每次读磁盘**：没有缓存层，每次 LLM 调用都重新读取 4 个文件
5. **代码已有 gid 参数但未利用**：`_load_character(gid)` 为后续改造留好了接口

---

## 二、设计目标

- 每个群可以独立配置一个活跃角色（或不启用角色）
- 角色的记忆按群分离（同一角色在不同群有不同记忆）
- 别名检测覆盖所有已分配角色的别名，精确匹配群
- 最小化对现有代码的侵入
- 向后兼容：未配置的群行为与当前完全一致

---

## 三、方案

### 第一步：配置结构调整

#### 1a. 新增 `config/group_characters.json`（群→角色映射）

```json
{
  "755471390": "soyo nagasaki",
  "123456789": "another character"
}
```

未配置的群回退到全局默认（`active_character.json` 中的值）。

#### 1b. 扩展 `active_character.json`

```json
{
  "enabled": true,
  "character": "soyo nagasaki",       // 全局默认（兼容）
  "admin_ids": ["784427550"],
  "group_admins": {                   // 新增：群级管理员（可选）
    "755471390": ["784427550"],
    "123456789": ["111222333"]
  }
}
```

保留全局 `enabled` / `character` 作为未配置群的默认值。

#### 1c. 记忆文件结构调整

**当前：**
```
config/characters/soyo nagasaki/memories.json
```

**改为：**
```
config/characters/soyo nagasaki/memories/
  ├── common.json          ← 角色通用记忆（所有群共享）
  ├── 755471390.json       ← A群专属记忆
  └── 123456789.json       ← B群专属记忆
```

迁移时把现有 `memories.json` 内容搬进 `common.json`，零破坏。

---

### 第二步：`decision_engine.py` 改造

| 函数 | 改动 |
|------|------|
| `get_active_character()` | 增加 `get_character_for_group(gid)` 方法，查映射表 + 回退全局默认 |
| `_load_character(gid)` | 根据 gid 查映射 → 加载对应角色 persona → 加载 `common.json` + `{gid}.json` 记忆合并注入 |
| `get_character_aliases()` | 返回所有已分配角色的别名映射 `{alias: gid}`，不只返回当前活跃角色 |
| `load_memories()` / `save_memories()` | 增加 `gid` 参数，支持按群加载/保存 |
| 新增缓存层 | 角色文件内容缓存 5-10 分钟，避免每次读磁盘 |

**`_load_character(gid)` 改造后逻辑：**

```
1. 查 group_characters.json[gid] → 找到角色名
2. 若未配置，回退 active_character.json 全局默认
3. 检查 enabled 开关
4. 加载 characters/{char}/persona.txt
5. 加载 memories/common.json（通用记忆）
6. 加载 memories/{gid}.json（群专属记忆，不存在则跳过）
7. 合并：通用记忆 + 群专属记忆
8. 加载 user_profiles.json[gid]（群友画像，不变）
9. 拼接 system_prompt
```

---

### 第三步：`reverse_bot.py` 改造

| 改动点 | 说明 |
|--------|------|
| 别名检测 | 调用新的 `get_all_character_aliases()` 获取所有已分配角色的别名，按消息所在 gid 精确匹配 |

当前代码位置：第 237-246 行，仅检查当前活跃角色别名。

---

### 第四步：`command_handler.py` 改造

| 改动点 | 说明 |
|--------|------|
| `_is_admin(uid)` | 增加群级管理员检查：全局 admin OR 当前群的群级 admin |
| 添加/删除/查看记忆 | 指定 gid，操作当前群的记忆文件 |
| `添加记忆` | 写入 `memories/{gid}.json`，不存在则创建 |
| `查看记忆` | 显示通用记忆 + 当前群专属记忆 |
| 新增指令 | `群角色分配 [群名] [角色名]` |
| 新增指令 | `群角色列表` |
| 新增指令 | `取消群角色 [群名]` |
| `切换角色` | 改为修改全局默认（未配置群使用） |
| 帮助系统 | 新增"群角色管理"子类别 |

---

### 第五步：`memory_manager.py` 改造

- 增加群选择交互
- 支持为指定群的指定角色编辑记忆
- 启动时列出已配置的群角色映射

---

### 第六步：数据迁移

1. 启动时检测 `config/characters/{char}/memories.json` 是否存在
2. 若存在且 `memories/` 目录不存在：
   - 创建 `memories/` 目录
   - 将 `memories.json` 内容复制为 `memories/common.json`
   - 删除原 `memories.json`
3. 初始化 `group_characters.json`：把当前全局角色分配给已知群
4. 迁移过程记录日志

---

## 四、改动量评估

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `config/group_characters.json` | **新增** | 群→角色映射 |
| `config/active_character.json` | 小改 | 增加 `group_admins` 字段 |
| `config/characters/*/memories/` | 结构迁移 | 现有 `memories.json` → `memories/common.json` |
| `decision_engine.py` | **中改** | 核心改造：群角色查找、记忆合并、缓存 |
| `reverse_bot.py` | 小改 | 别名检测逻辑 |
| `command_handler.py` | **中改** | 新指令、群管理员、记忆按群操作 |
| `memory_manager.py` | 中改 | 群选择交互 |

---

## 五、风险点

1. **记忆迁移**：需确保迁移脚本不丢失数据，迁移后可回滚
2. **向后兼容**：未配置 `group_characters.json` 时行为应与当前完全一致
3. **并发写入**：当前 JSON 写入无锁机制，多群同时修改记忆时需注意（但用户场景下概率低）

---

## 六、实施建议

**原则：渐进式改造，分阶段实施**

### Phase 1：核心功能（MVP）
- 新增 `group_characters.json` 映射
- 改造 `_load_character(gid)` 支持群角色查找
- 记忆暂保持共享（不改记忆文件结构）
- 别名检测按群匹配

### Phase 2：记忆分离
- 记忆文件结构调整（`memories.json` → `memories/common.json`）
- 数据迁移脚本
- 命令处理器按群读写记忆

### Phase 3：精细化
- 群级管理员
- 缓存层
- 新的管理指令（群角色分配/列表/取消）
- `memory_manager.py` 群选择交互

---

> **待确认**：记忆是否一定要按群分离？还是先只做角色按群分配、记忆共享？