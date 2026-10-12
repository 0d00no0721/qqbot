# V1 识图能力底座（vision core）— 完成

备份：`qq_native_re/patches/vision_backup_20261012-110536/`（llm_pool.py、model_pool.json、
test_pool_harden.py.BEFORE/V1_edit）

## 四个文件的关系与接口
`model_pool.json.vision_models`（白名单：deepseek-flash / glm-5.3-flash / qwen3.8-chat）
→ `llm_pool.chat(..., vision=True)` 只把候选收窄到白名单（tier 顺序/冷却/审计/预算不变，空名单回退全链）
→ `image_utils`（parse_cq_images / fetch_image / to_data_url / is_sticker）产出 data URL
→ `vision.describe(data_url, hint) -> (ok, text)`，label="vision"、max_tokens=300、temperature=0.3，
空正文与 `LLMPoolExhausted` 一律降级为 `(False, "")`，敏感图只回「（图片内容不便描述）」。

## 演练实际输出（`_v1_drill.py`，exit=0，全文 `V1_drill_output.txt`）
真实日志 21 行 `[CQ:image]` → 19 张去重图，元数据解析正确（含 `sub_type=1/[动画表情]` 判贴图）；
真实直链 **rkey 已过期**：HTTP 400 `retcode=-5503007 download url has expired` → 按预案走生成图兜底；
预检分支：`file_size=271533 > max_bytes=1024` → 0.000s 返回 None（未发请求）。
三张内容已知生成图，全部 `ok=True`：
1. 纯蓝图 → 「这张图片是纯蓝色的，没有可见的图案、文字或物体。」
2. 四宫格(左上红/右上绿/左下黄/右下白) → 「图片分成四块：左上为红色，右上为绿色，左下为黄色，右下为白色。」（位置全对）
3. 高熵噪点 1200x900（3,243,482B → 473,020B，**6.9x**）→ 「整张图是均匀分布的彩色随机噪点，没有可辨认的物体、轮廓或场景，属于随机噪点而非照片。」

## 白名单 order 证据
`vision=True` → 2 项：`preferred/deepseek-flash`、`4/glm-5.3-flash`（默认全链 9 项）；
`qwen3.8-flash-next` 已被排除、`qwen3.8-chat` 不在 tiers 故不出现。反证：临时白名单设单模型 → order 只剩它；
白名单 `[]` → 回退全链。**不传 vision** 与备份模块（对齐 config/active_model 路径 + 清冷却后）**逐字一致**（含 tier 标记），
剔除末尾新增参数后签名亦逐字一致。

## 两个套件
`test_llm_pool.py` exit=0（全部通过）、`test_pool_harden.py` exit=0（全部通过）。
**报告一处必要改动**：H6 逐字比对 `chat` 签名，故给 `vision` 加了"有意追加参数"豁免——必须是末尾
keyword-only、默认 False，且**从签名文本逐字剔除后仍须与 W1 备份逐字一致**（不是放宽）。
已备份改前版本。附带修掉一个真 bug：用 `Signature.replace` 重建会使注解失去引号，导致所有签名误报 FAIL。

## 未动其它文件
仅改 `scripts/llm_pool.py`、`config/model_pool.json`、`scripts/test_pool_harden.py`；新增
`scripts/image_utils.py`、`scripts/vision.py`、`findings/vision/{_v1_drill.py,V1_drill_output.txt,V1_core.md}`。
未碰 reverse_bot.py / NapCat / .env / persona，未重启任何进程。`import reverse_bot` → OK。
key 全程 `sk-personal-****`，未落盘 base64。
