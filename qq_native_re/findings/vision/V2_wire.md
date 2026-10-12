# V2 识图接线（vision wire）— 交付报告

结论：**代码已接线完毕并全部验证通过；本次不重启 bot，新代码下次重启后才生效。**

## 0. 备份与纪律
- 备份目录：`qq_native_re/patches/vision_wire_backup_20261012-121816/`（`reverse_bot.py` 20425B + `config/group_features.json` 475B）
- 本波改动文件**仅 4 个**：新建 `scripts/image_cache.py`、`qq_native_re/findings/vision/_v2_drill.py`、`qq_native_re/findings/vision/_v2_drill_output.txt`；改 `reverse_bot.py`、`config/group_features.json`、`scripts/commands/_state.py`。
- 未动：`llm_pool.py`、`config/model_pool.json`、`decision_engine.py`、`.env`、persona、NapCat 配置与二进制、`scripts/commands/system.py`（群管理功能清单**未同步 vision**，见 §5）。未重启 bot / NapCat。

## 1. 新增模块 `scripts/image_cache.py`（本地消息缓存，解引用用）
接口：`remember(gid, message_id, sender_uid, nickname, images, raw_message)`、`by_id(gid, mid)`、`recent_by_sender(gid, uid, within=120)`、`recent(gid, within=120)`、`seen_hash(gid, hash, within=60)`、`stats(gid)`、`clear(gid)`、`select_candidates(raw, gid, uid, …) -> (候选, 路径)`、`cq_reply_id(raw)`、`image_hash(meta)`、`describe_entry(entry)`；另有 `MessageCache` 类可独立实例化。
- 每群环形缓冲 `RING_MAX=200` / `TTL_SECONDS=600`（`time.monotonic` 计时）；`threading.RLock`；查询接口不抛异常。
- **只存元数据**（`parse_cq_images` 产物），绝不存字节/base64；`image_hash` 推算的签名**不含 rkey**。

## 2. `reverse_bot.py` 改动位置
| 行 | 改动 |
|---|---|
| 17 | `import re` 提到模块级（`_cq_reply_id` 要用；原先只在函数内 import） |
| 66 | `_FEATURE_KEYS` 加入 `"vision"` |
| 228-233 | 识图常量：`VISION_MAX_PER_HOUR=10`、`VISION_RATE_WINDOW=3600`、`VISION_RECENT_WINDOW=120`、`VISION_SEEN_WINDOW=60`、`VISION_PICK_MAX=3` |
| 235-245 | `_cq_reply_id()`：按 CQ 结构解析 `[CQ:reply,id=…]`（不用全局 `id=` 正则，避免误命中） |
| 248-301 | `_describe_triggered_image()`：限流 → 选图 → 三道闸门 → 识图，**全程只返回文本、失败只记一行日志** |
| 341-361 | 计算 `clean_message`/`bot_mentioned` → **只要有图就无条件 `image_cache.remember(...)`** → 守卫改为 `if not clean_message and not bot_mentioned: return`（纯图无 @ 只缓存不响应） |
| 401-426 | 识图触发块（别名检测之后、决策引擎之前）→ 注入 `text_for_bot`（`not clean_message` 或 `clean_or_raw` 以 CQ 码开头 → 只给描述本身） |
| 427-431 | 决策引擎改用 `text_for_bot`（`add_message`/`should_reply` 都传它） |

`scripts/commands/_state.py`：122-148 加 `_vision_lock` + `vision_usage` + `vision_rate_ok(gid, limit, window)` 滑动窗口限流。任务书写的是 `reverse_bot.py` + `commands/_state.py` + `group_features.json` + `image_cache.py` —— **限流计数落在 `_state.py`**（与既有 `yau_usage`/`zhouli_usage` 同址），未把 `scripts/commands/` 改到别处。
注入契约：`text_for_bot = 原文 + "\n[图片: 描述]"`；纯图+@时 `text_for_bot` **只有** `[图片: 描述]`（顺带修掉原先会喂给模型的原始 CQ 码）；`clean_message` 不动（+1/历史/画像仍存用户原文）。

## 3. 配置与开关
`config/group_features.json`：`284517232` 新增 `"vision": true`；`1055881417` 未加（缺键 → `_feature_on` 退化为 False，白名单语义）。JSON 语法已校验。

## 4. 验证（`qq_native_re/findings/vision/_v2_drill.py` 可复跑，原文见 `_v2_drill_output.txt`）
**35/35 通过，exit=0。**
- **三条路径**：① 本消息带图 → `路径=本消息 选中=['pic_one.jpg']`；② `[CQ:reply,id=9002]` → `路径=引用 选中=['pic_two.jpg']`（与①不同图，可区分）；③ 无引用直接 @ → `路径=最近-同人`；④ 陌生人 @ → `路径=最近-本群`。缓存 `2 条/2 图/ring 200/TTL 600`；TTL 过期后 `by_id → None`。
- **闸门**：功能位 `284517232 True`／其它群与未配置群 `False`；纯图无 @ → 只缓存不响应；贴图（`sub_type=1`+`&#91;动画表情&#93;`）→ `[识图] 跳过（贴图/表情）`，被引用时例外放行；同图第二次 → `[识图] 跳过（同图去重）`；限流前 10 张放行、第 11 张 `[识图] 跳过（限流：本群 1h 内已识 10 张，上限 10）`，窗口滚动后恢复。
- **降级**：真实 NapCat 日志里的过期直链 → `fetch_image` 返回 `None`（659ms，不抛异常）；空/非法直链、非图片字节同样 `None`；生产函数对假直链只打 `[识图] 跳过（下载失败/直链过期：pic_two.jpg）`。
- **实弹**：Pillow 现场生成四宫格（`大肥鱼` / 红色准星 / `12345` / `HELLO`）→ `to_data_url`（13624B PNG → 29037B）→ `vision.describe` 原文：**「一张白底黑框的田字格图，左上格有黑色文字“大肥鱼”，右上格是红色圆圈加十字的瞄准镜图形，左下格有黑色数字“12345”，右下格有黑色英文“HELLO”。」** 三个关键词全部命中。
- **端到端**（`handle_message` 真跑，假 websocket，**不发任何消息**、不碰生产群 284517232）：带图+@ → 决策引擎收到 `这是啥图\n[图片: …]`；先发纯图（无 @）只入缓存、`sent=[]` → 再引用它提问 → 走 `引用` 路径识图；纯图+@ → 决策引擎收到纯 `[图片: …]`（无 CQ 码）；缓存条目 `mid=… uid=10001 nick=甲 images=[e2e_pic.jpg(sub_type=0)]`，**无 base64/data URL**；普通文字消息不写缓存。
- **回归**：`import reverse_bot` → OK（exit=0）；`scripts/test_llm_pool.py` → 全部通过 exit=0；`scripts/test_pool_harden.py` → 全部通过 exit=0。

## 5. 注意事项 / 遗留
1. **下次重启 bot 才生效**：当前运行中的 bot 没被重启、内存里仍是旧代码（探活证据：`127.0.0.1:8080` 仍 LISTENING；`logs/bot.2026-10-12.log` 仍由运行中的进程独占写入）。重启由用户执行。
2. 实测取证修正：真实贴图的 `summary` 是 `&#91;动画表情&#93;`（HTML 转义），所以 `parse_cq_images` 能正常取到 url；若括号未转义，CQ 码会被 `]` 提前截断导致 url 丢失——两种形态都不会误识贴图（演练里都验了）。
3. 限流采用「先占额再识图」：未识到图也占一格（宁可少识不可无限识）；单次只识 1 张（候选里取最新），拿够 3 张候选会命中限流更快。
4. `scripts/commands/system.py` 的群功能清单/CLI **本波未动**（按要求另单处理）：CLI 若按固定键列表展示开关，重启后 `vision` 可能不在列表里，但 `group_features.json` 里的 `vision: true` 仍会被 `_feature_on` 正确读到。
5. 实弹与端到端演练各消耗了少量视觉模型配额（约 4 次调用），audit 日志可见 `label=vision`。
