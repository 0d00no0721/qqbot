#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立 QQ 群机器人 - 今天吃什么（反向 WebSocket 模式）
脚本作为服务端，NapCat 作为客户端主动连接
"""

import os

# 加载 .env 文件（必须在 os.getenv 之前）
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import asyncio
import json
import logging
import re
import sys
import traceback
import time
from collections import defaultdict, deque
from scripts.message_queue import MessageQueue
from scripts.request_limits import limits, RequestState, request_state
from datetime import datetime
from typing import Dict

import websockets

# ========== 并发安全锁 ==========
_plus_one_locks: dict = {}
_plus_one_locks_guard = asyncio.Lock()


async def _get_plus_one_lock(gid: str) -> asyncio.Lock:
    """获取或创建某群的 +1 检测锁（双重检查）"""
    if gid not in _plus_one_locks:
        async with _plus_one_locks_guard:
            if gid not in _plus_one_locks:
                _plus_one_locks[gid] = asyncio.Lock()
    return _plus_one_locks[gid]

# ========== 发言决策引擎 ==========
from scripts.decision_engine import DecisionEngine

# ========== 用户画像 ==========
from scripts.user_profile import (
    PROFILE_ENABLED, load_profiles, record_message, get_profile, update_profiles,
    PROFILE_UPDATE_INTERVAL, set_fallback_api_key,
    set_napcat_connected, has_pending_retry, retry_pending_updates,
)

# ========== 命令处理器 ==========
from scripts.command_handler import (
    handle_command, process_voice_message,
    _mystery_queue, _dedup_lock,
    BOT_QQ, VERSION,
    init_handlers,
)

# ========== 脚本所在目录 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== 群功能开关（读取 config/group_features.json） ==========
import threading
_features_cache: dict = {"data": None, "mtime": 0.0}
_features_lock = threading.Lock()
_FEATURES_FILE = os.path.join(SCRIPT_DIR, "config", "group_features.json")
_FEATURE_KEYS = ("decision_engine", "plus_one", "commands", "newcomer_welcome", "profile_record", "vision")


def _get_group_features(gid: str) -> dict:
    """读取某群的功能开关，带文件 mtime 缓存。未配置的群返回空 dict。"""
    try:
        mtime = os.path.getmtime(_FEATURES_FILE)
    except OSError:
        return {}
    with _features_lock:
        if _features_cache["data"] is None or mtime != _features_cache["mtime"]:
            try:
                with open(_FEATURES_FILE, "r", encoding="utf-8") as f:
                    _features_cache["data"] = json.load(f)
                _features_cache["mtime"] = mtime
            except (OSError, json.JSONDecodeError):
                return {}
        entry = _features_cache["data"].get(gid, {})
    if not isinstance(entry, dict):
        return {}
    feat = entry.get("features", {})
    return feat if isinstance(feat, dict) else {}


def _feature_on(gid: str, key: str) -> bool:
    """查询某群某功能是否启用。未配置的群一律返回 False（白名单语义）。"""
    feat = _get_group_features(gid)
    if not feat:
        return False
    return bool(feat.get(key, False))

# ========== 日志配置 ==========
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

bot_logger = logging.getLogger("qqbot")
bot_logger.setLevel(logging.INFO)

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
_console.setLevel(logging.INFO)
bot_logger.addHandler(_console)

_LOG_DATE = datetime.now().strftime("%Y-%m-%d")
LOG_FILE = os.path.join(LOG_DIR, f"bot.{_LOG_DATE}.log")


def _cleanup_old_logs(log_dir: str, keep_days: int = 30) -> None:
    """删除超过 keep_days 天的旧日志文件。"""
    import glob as _glob
    cutoff = datetime.now().timestamp() - keep_days * 86400
    for pattern in [os.path.join(log_dir, "bot.*.log"),
                    os.path.join(log_dir, "bot.log*")]:
        for f in _glob.glob(pattern):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except OSError:
                pass

_cleanup_old_logs(LOG_DIR)

_file_handler = None
try:
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _file_handler.setLevel(logging.INFO)
    bot_logger.addHandler(_file_handler)
except OSError as _e:
    # 例：PermissionError（logs/ 目录被 ACL 拒绝）——降级为仅控制台日志，不阻断 import
    _console.stream.write(f"[警告] 无法写入文件日志 logs\\bot.{_LOG_DATE}.log（权限不足），已降级为仅控制台日志\n")

# ========== 配置 ==========
HOST = "0.0.0.0"
PORT = 8080

# ========== API Key（从 .env 读取） ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# ========== 决策引擎专用配置 ==========
# 注：以下三个值在 DecisionEngine.__init__ 函数体内从未被消费（惰性值，实弹出口由
#     scripts/llm_pool.py 统一决定），此处仅为兼容既有代码结构保留；值已改指本地网关，
#     确保全仓不再残留任何云端字面量。
DECISION_API_KEY = os.getenv("DECISION_API_KEY", "")
DECISION_API_BASE = "http://127.0.0.1:8090/v1"

# ========== 发言决策引擎 ==========
decision_engine = DecisionEngine(
    api_key=DECISION_API_KEY,
    api_base_url=DECISION_API_BASE,
    api_model="deepseek-flash",
    fallback_api_key=DEEPSEEK_API_KEY,
)

set_fallback_api_key(DEEPSEEK_API_KEY)

# 群聊历史缓存
group_histories = defaultdict(lambda: deque(maxlen=50))

# +1 检测
recent_messages: Dict[str, tuple] = {}
repeated_messages: Dict[str, str] = {}

# 运行时状态
current_websocket = None
_message_tasks = {}
_processed_msg_order = deque()
_receipt_tails = {}
_receipt_pending = set()


# ========== 消息发送 ==========
async def send_message(websocket, group_id: int, message: str, at_user: str = None):
    """发送消息，连接断开时不向上抛异常。"""
    if at_user:
        message = f"[CQ:at,qq={at_user}] {message}"
    payload = {
        "action": "send_group_msg",
        "params": {"group_id": group_id, "message": message}
    }
    try:
        await websocket.send(json.dumps(payload))
        return True
    except websockets.exceptions.ConnectionClosed:
        bot_logger.warning("  ⚠️  发送失败: 连接已断开，消息已丢弃")
        return False


async def send_group_image(websocket, group_id: int, image_path: str):
    """发送图片消息（base64 内联）"""
    import base64
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": group_id,
            "message": [{"type": "image", "data": {"file": f"base64://{b64}"}}]
        }
    }
    await websocket.send(json.dumps(payload))


async def send_group_file(websocket, group_id: int, file_path: str):
    """发送文件消息（base64 内联）"""
    import base64
    file_name = os.path.basename(file_path)
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": group_id,
            "message": [{"type": "file", "data": {"file": f"base64://{b64}", "name": file_name}}]
        }
    }
    await websocket.send(json.dumps(payload))


message_queue = MessageQueue(send_message)


# ========== 识图（V2 接线） ==========
# 纪律（PM 定稿）：
#   - 只认 `bot_mentioned`（@bot / 别名命中）；贴图默认不识别（被引用提问除外）；
#   - 同图哈希窗口内只识一次；每群滑动窗口限流（≤ VISION_MAX_PER_HOUR 张/小时）；
#   - 直链 rkey 会过期 → **收到消息就立刻下载**，失效/失败/描述空一律静默降级，
#     只写一行日志，**绝不阻断文本链**；
#   - 成功后把 `[图片: 描述]` 拼进消息文本交给既有决策链（复用全部现有逻辑），
#     **绝不把 base64/字节塞进 decision_engine 的上下文缓冲**。
VISION_MAX_PER_HOUR = 10
VISION_RATE_WINDOW = 3600    # 限流窗口（秒）
VISION_RECENT_WINDOW = 120   # 路径③④：图后直接 @ 的时间窗（秒）
VISION_SEEN_WINDOW = 60      # 同图去重窗口（秒）
VISION_PICK_MAX = 3          # 单次最多识几张


def _cq_reply_id(raw_message: str) -> str:
    """抽取 `[CQ:reply,id=…]` 的被引用 message_id（按 CQ 码结构解析，避免误命中）。"""
    for match in re.finditer(r'\[CQ:(?P<type>[a-zA-Z_]+)(?P<params>(?:,[^\],]*)*)\]', str(raw_message or "")):
        if match.group("type").lower() != "reply":
            continue
        for chunk in match.group("params").split(","):
            key, _, value = chunk.partition("=")
            if key.strip() == "id" and value.strip():
                return value.strip()
    return ""


async def _describe_triggered_image(gid_str: str, message_images: list, raw_message: str,
                                    uid_str: str, clean_message: str, candidates_snapshot=None) -> list:
    """按「本消息 → 引用 → 最近-同人 → 最近-本群」选图并识图。

    返回 `[图片: 描述]` 结果文本列表（0~1 条）。**任何失败都只是返回空列表**，
    由调用层决定要不要拼进消息文本；本函数不发送消息、不改全局状态（除限流计数）。
    """
    from scripts import image_cache, image_utils, vision
    from scripts.commands import _state

    # ---- 限流（先占额再识图，宁可少识不可无限识） ----
    allowed, used = _state.vision_rate_ok(gid_str, VISION_MAX_PER_HOUR, VISION_RATE_WINDOW)
    if not allowed:
        bot_logger.info(f"[识图] 跳过（限流：本群 1h 内已识 {used} 张，上限 {VISION_MAX_PER_HOUR}）")
        return []

    candidates, source = candidates_snapshot if candidates_snapshot is not None else image_cache.select_candidates(
        raw_message, gid_str, uid_str,
        message_images=message_images,
        within=VISION_RECENT_WINDOW,
        limit=VISION_PICK_MAX,
    )
    if not candidates:
        bot_logger.info(f"[识图] 跳过（没有可用图片：引用未命中缓存且窗口内无图）")
        return []

    bot_logger.info(f"[识图] 触发：路径={source} 候选={len(candidates)} 张")

    described: list = []
    for meta in candidates:
        tag = str(meta.get("file") or image_cache.image_signature(meta) or "?")
        # 闸门 1：贴图/动画表情默认跳过（引用路径来的不跳——用户明确引用了它）
        if source != "引用" and image_utils.is_sticker(meta):
            bot_logger.info(f"[识图] 跳过（贴图/表情：{tag}）")
            continue
        # 闸门 2：同图哈希窗口内去重
        sig = image_cache.image_hash(meta)
        if sig and image_cache.seen_hash(gid_str, sig, VISION_SEEN_WINDOW):
            bot_logger.info(f"[识图] 跳过（同图去重：{tag}）")
            continue
        # 闸门 3：直链可能已过期（rkey 时效）→ fetch_image 返回 None，静默降级
        img_bytes = image_utils.fetch_image(meta)
        if not img_bytes:
            bot_logger.info(f"[识图] 跳过（下载失败/直链过期：{tag}）")
            continue
        data_url = image_utils.to_data_url(img_bytes)
        if not data_url:
            bot_logger.info(f"[识图] 跳过（图片解码失败：{tag}）")
            continue
        ok, desc = await vision.describe(data_url, hint=clean_message)
        if not ok or not desc:
            bot_logger.info(f"[识图] 跳过（识别失败/空描述：{tag}）")
            continue
        described.append(f"[图片: {desc}]")
        bot_logger.info(f"[识图] 成功：{tag} → {desc}")
        break  # 一次只识一张：多条候选里取最新的那张即可
    return described


# ========== 消息处理 ==========
async def handle_message(websocket, data: dict):
    t_req = data.get("_received_at", time.time())
    group_id = data["group_id"]
    user_id = data["user_id"]
    raw_message = data["raw_message"].strip()
    if data.get("self_id") == user_id:
        return

    # ===== 消息去重 =====
    msg_id = data.get("message_id")
    if msg_id is not None:
        from scripts.command_handler import processed_msg_ids, PROCESSED_MSG_MAX
        async with _dedup_lock:
            if msg_id in processed_msg_ids:
                bot_logger.info(f"[去重] 跳过重复消息 mid={msg_id}")
                return
            if not processed_msg_ids:
                _processed_msg_order.clear()
            processed_msg_ids.add(msg_id)
            _processed_msg_order.append(msg_id)
            while len(processed_msg_ids) > PROCESSED_MSG_MAX and _processed_msg_order:
                processed_msg_ids.discard(_processed_msg_order.popleft())

    # 记录已知群
    from scripts.command_handler import known_groups
    known_groups.add(str(group_id))

    # ===== 群功能开关：未启用的群完全不响应 =====
    gid_str = str(group_id)
    if not any(_feature_on(gid_str, k) for k in _FEATURE_KEYS):
        return

    # ===== 图片解析 + 本地消息缓存（解引用用） =====
    # 只要有图就无条件入缓存：手机端「先发图 → 再引用提问」时，提问那条消息里没有图,
    # 只能靠 message_id → 图片元数据 的本地映射命中（OneBot get_image 是请求-响应式，
    # 且历史直链 rkey 分钟级过期，实测不可用）。**只存元数据，绝不存字节/base64。**
    from scripts import image_cache, image_utils, vision  # noqa: F401  (vision 供 _describe_triggered_image 使用)
    uid_str = str(user_id)
    nickname = data.get("sender", {}).get("nickname", "")
    message_images = image_utils.parse_cq_images(raw_message)
    if message_images:
        image_cache.remember(
            gid_str, data.get("message_id"), uid_str, nickname,
            images=message_images, raw_message=raw_message,
        )

    # 候选在接收阶段固定，慢语音转写或排队期间的新图片不能改变本条回应目标。
    candidates = None
    if _feature_on(gid_str, "vision"):
        images, source = image_cache.select_candidates(raw_message, gid_str, uid_str,
            message_images=message_images, within=VISION_RECENT_WINDOW, limit=VISION_PICK_MAX)
        candidates = ([dict(image) for image in images], source)

    # ===== 语音消息转文字 =====
    if "[CQ:record" in raw_message:
        raw_message = await process_voice_message(raw_message)
    clean_message = re.sub(r'\[CQ:\w+,[^\]]*\]', '', raw_message).strip()
    bot_mentioned = f"[CQ:at,qq={BOT_QQ}]" in raw_message

    # ===== 纯图片/表情消息忽略（无文字且没 @bot → 只缓存不响应） =====
    if not clean_message and not bot_mentioned:
        return

    # ===== 命令处理 =====
    if bot_mentioned and _feature_on(gid_str, "commands"):
        handled = await handle_command(websocket, data, clean_message, raw_message)
        if handled:
            return

    # 慢转写不会令后到的聊天先写入上下文；命令已在独立通道处理。
    previous = data.get("_receipt_previous")
    if previous is not None:
        await asyncio.shield(previous)

    # ===== 名字/别名检测 =====
    clean_or_raw = clean_message or raw_message
    if not bot_mentioned:
        all_aliases = decision_engine.get_all_character_aliases()
        if all_aliases:
            msg_lower = clean_or_raw.lower()
            for alias_lower, alias_gid in all_aliases.items():
                if alias_lower in msg_lower:
                    bot_mentioned = True
                    bot_logger.info(f"[名字匹配] '{alias_lower}' → 视为 @bot，群{alias_gid}")
                    break

    # ===== 更新历史缓存 =====
    group_histories[gid_str].append({
        "content": clean_message or raw_message,
        "user_id": uid_str,
        "timestamp": t_req,
    })

    # ===== 用户画像：缓冲发言 =====
    if PROFILE_ENABLED and _feature_on(gid_str, "profile_record"):
        record_message(gid_str, uid_str, clean_message or raw_message)

    if _feature_on(gid_str, "decision_engine"):
        context_entry = decision_engine.add_message(gid_str, uid_str, nickname, clean_or_raw,
                                                    timestamp=t_req)
        if PROFILE_ENABLED:
            profile_text = get_profile(gid_str, uid_str)
            if profile_text:
                decision_engine.add_message(gid_str, "system", "系统", f"[用户画像: {profile_text}]",
                                            timestamp=t_req)
        snapshot = dict(data)
        snapshot.pop("_receipt_previous", None)
        snapshot["sender"] = dict(data.get("sender") or {})
        message_queue.submit(websocket, snapshot,
            lambda: _run_chat_message(websocket, snapshot, clean_message, raw_message,
                                     bot_mentioned, list(message_images), context_entry, candidates),
            explicit=bot_mentioned)

    # ===== +1 检测 =====
    gid = str(group_id)
    if _feature_on(gid_str, "plus_one"):
        plus_one_lock = await _get_plus_one_lock(gid)
        async with plus_one_lock:
            last_user_id, last_msg = recent_messages.get(gid, (None, None))
            prev_repeated = repeated_messages.get(gid)

            if (
                last_msg is not None
                and last_msg == clean_message
                and str(last_user_id) != str(user_id)
                and clean_message != prev_repeated
            ):
                await send_message(websocket, group_id, clean_message)
                repeated_messages[gid] = clean_message
            elif clean_message != last_msg:
                repeated_messages.pop(gid, None)

            recent_messages[gid] = (user_id, clean_message)



async def _run_chat_message(websocket, data, clean_message, raw_message, bot_mentioned,
                            message_images, context_entry, candidates_snapshot):
    group_id = data["group_id"]
    gid_str, uid_str = str(group_id), str(data["user_id"])
    nickname = data.get("sender", {}).get("nickname", "")
    clean_or_raw = clean_message or raw_message
    t_req = data.get("_received_at", time.time())
    if not _feature_on(gid_str, "decision_engine"):
        return
    # ===== 识图（闸门：@bot/别名 + vision 功能位 + 限流 + 贴图过滤 + 同图去重） =====
    # 位置刻意放在"别名检测之后、决策引擎之前"：这样别名命中也算触发（与 @ 同权），
    # 且 `clean_or_raw` 已经算好，注入描述后可与历史缓存里的原文精确比对（不污染 +1/画像）。
    text_for_bot = clean_or_raw
    if bot_mentioned and _feature_on(gid_str, "vision"):
        try:
            vision_desc = await _describe_triggered_image(
                gid_str, message_images, raw_message, uid_str, clean_message, candidates_snapshot,
            )
        except Exception as e:
            # 识图是增强项：任何意外都只记一行日志，文本链照常走
            bot_logger.info(f"[识图] 出错（已忽略，不影响文本回复）: {e}")
            vision_desc = []
        if vision_desc:
            # 方案 a（PM 定稿）：把 `[图片: 描述]` 拼进消息文本，复用既有决策链
            # （人设/记忆/密度/冷却全部生效）；**禁止**把图字节/base64 放进上下文缓冲。
            # 只改"给决策引擎的文本"，不动 clean_message（保持 +1 检测/历史缓存/画像
            # 里存的仍是用户原文，避免描述文本被当成复读内容）。
            if not clean_message or clean_or_raw.startswith("[CQ:"):
                # 纯图（或图+@）消息：`clean_or_raw` 退化成的是**原始 CQ 码**，
                # 直接喂给模型既难看又浪费 token → 只给描述本身
                text_for_bot = vision_desc[0]
                bot_logger.info("[识图] 本条消息只有图 + @：注入描述后进入决策链")
            else:
                text_for_bot = f"{clean_or_raw}\n{vision_desc[0]}".strip()

    # ===== 决策引擎 =====
    if _feature_on(gid_str, "decision_engine"):
        t_decision = time.time()
        try:
            if context_entry is not None:
                context_entry["content"] = text_for_bot

            should_reply, reply_text = await decision_engine.should_reply(
                message=text_for_bot,
                user_id=uid_str,
                group_id=gid_str,
                is_at_bot=bot_mentioned,
                sender_nickname=nickname,
            )

            elapsed_ms = int((time.time() - t_decision) * 1000)
            if elapsed_ms > 500:
                bot_logger.info(f"[耗时] 决策引擎耗时 {elapsed_ms}ms (群{gid_str})")

            if should_reply and reply_text:
                at_user = uid_str if bot_mentioned else None
                await send_message(websocket, group_id, reply_text, at_user=at_user)
                total_ms = int((time.time() - t_req) * 1000)
                info = decision_engine.get_debug_info()
                bot_logger.info(f"[决策] 群 {gid_str} 回复（总耗时 {total_ms}ms）| "
                      f"密度={info.get('密度','?')} 分类={info.get('分类结果','?')} "
                      f"阈值={info.get('阈值','?')} 决策={info.get('最终','?')}")
        except Exception as e:
            bot_logger.info(f"[决策引擎] 出错: {e}")
            traceback.print_exc()



async def handle_group_increase(websocket, data: dict):
    """处理新成员入群通知"""
    group_id = data["group_id"]
    new_user_id = data["user_id"]
    from scripts.command_handler import known_groups
    known_groups.add(str(group_id))
    if not _feature_on(str(group_id), "newcomer_welcome"):
        return
    reply = '欢迎新人~可以发送”@bot 帮助”了解更多~（记得长按头像）'
    await send_message(websocket, group_id, reply, at_user=str(new_user_id))


# ========== 后台任务 ==========
async def profile_update_task():
    try:
        await asyncio.sleep(300)
        while True:
            if PROFILE_ENABLED:
                bot_logger.info("[画像] 开始定时更新...")
                try:
                    await update_profiles()
                    if has_pending_retry():
                        bot_logger.info("[画像] 有待重试队列，5 分钟后短周期重试...")
                        await asyncio.sleep(300)
                        if has_pending_retry():
                            await retry_pending_updates()
                except Exception as e:
                    bot_logger.info(f"[画像] 定时更新异常: {e}")
                    traceback.print_exc()
            await asyncio.sleep(PROFILE_UPDATE_INTERVAL)
    except asyncio.CancelledError:
        bot_logger.info("[画像] 更新任务已取消")
        raise


async def mystery_number_task():
    """神秘数字排队处理器"""
    from scripts.command_handler import _mystery_queue, find_valid_number
    global _mystery_processing
    while True:
        try:
            item = await _mystery_queue.get()
            _mystery_processing = True
        except asyncio.CancelledError:
            break

        websocket, group_id, uid_str = item
        try:
            result = await find_valid_number()
            if result[0] == "success":
                await send_message(websocket, group_id,
                    f"今天可以看{result[1]}，祝起飞顺利~", at_user=uid_str)
            elif result[0] == "network_error":
                await send_message(websocket, group_id,
                    "不好意思，网络不支持哦~", at_user=uid_str)
            else:
                await send_message(websocket, group_id,
                    "连着3次都没有找到合适的呢，今天运气不佳，建议不要起飞哦~",
                    at_user=uid_str)
        except Exception as e:
            bot_logger.info(f"[神秘数字] 处理失败: {e}")
            traceback.print_exc()
            try:
                await send_message(websocket, group_id,
                    "神秘数字召唤失败，请稍后再试~", at_user=uid_str)
            except Exception:
                pass
        finally:
            _mystery_queue.task_done()

        await asyncio.sleep(5)
        _mystery_processing = False


def _start_message(websocket, data):
    raw = data.get("raw_message", "")
    if (not isinstance(raw, str) or not data.get("group_id") or not data.get("user_id")
            or not isinstance(data.get("sender", {}), dict) or data.get("self_id") == data.get("user_id")):
        return False
    if max(len(_message_tasks), len(_receipt_pending)) >= limits.get()["max_message_tasks"]:
        mentioned = f"[CQ:at,qq={BOT_QQ}]" in raw
        if not mentioned:
            mentioned = any(alias in raw.lower() for alias in decision_engine.get_all_character_aliases())
        if mentioned and _feature_on(str(data.get("group_id", "")), "decision_engine"):
            message_queue.reject(websocket, data)
        return False
    snapshot = dict(data)
    snapshot["sender"] = dict(data.get("sender") or {})
    snapshot["_received_at"] = time.time()
    snapshot["_received_monotonic"] = time.monotonic()
    key = (websocket, str(data["group_id"]))
    previous = _receipt_tails.get(key)
    snapshot["_receipt_previous"] = previous
    completed = asyncio.get_running_loop().create_future()
    _receipt_tails[key] = completed
    _receipt_pending.add(completed)
    task = asyncio.create_task(_safe_handle_message(websocket, snapshot))
    _message_tasks[task] = websocket

    def release(_):
        completed.set_result(None)
        _receipt_pending.discard(completed)
        if _receipt_tails.get(key) is completed:
            _receipt_tails.pop(key, None)

    def finished(done):
        _message_tasks.pop(done, None)
        if previous is not None and not previous.done():
            previous.add_done_callback(release)
        else:
            release(None)
    task.add_done_callback(finished)
    return True


async def _safe_handle_message(websocket, data: dict):
    state = RequestState(remaining_wait=limits.get(data.get("group_id"))["queue_wait_seconds"])
    token = request_state.set(state)
    try:
        await handle_message(websocket, data)
    except Exception as e:
        bot_logger.info(f"[task] handle_message 异常: {e}")
        traceback.print_exc()
    finally:
        if state.attempts:
            bot_logger.info("[指令耗时] queue_ms=%d model_ms=%d attempts=%d",
                            int(state.model_wait * 1000), int(state.model_time * 1000), state.attempts)
        request_state.reset(token)


async def _safe_handle_group_increase(websocket, data: dict):
    try:
        await handle_group_increase(websocket, data)
    except Exception as e:
        bot_logger.info(f"[task] handle_group_increase 异常: {e}")
        traceback.print_exc()


# ========== WebSocket 服务端 ==========
async def handler(websocket, *args):
    global current_websocket
    current_websocket = websocket
    set_napcat_connected(True)

    divider = "╔" + "══════════════════════════════════════" + "╗"
    divider_mid = "║"
    divider_end = "╚" + "══════════════════════════════════════" + "╝"
    bot_logger.info("")
    bot_logger.info(divider)
    bot_logger.info(divider_mid + "  ⚡ 客户端已连接: " + str(websocket.remote_address).ljust(32) + divider_mid[-1])
    bot_logger.info(divider_end)
    bot_logger.info("")

    if has_pending_retry():
        bot_logger.info("[画像] NapCat 重连，触发待更新队列重试")
        asyncio.create_task(retry_pending_updates())

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                post_type = data.get("post_type")
                if post_type == "message":
                    if data.get("message_type") == "group":
                        _start_message(websocket, data)
                elif post_type == "notice":
                    if data.get("notice_type") == "group_increase":
                        asyncio.create_task(_safe_handle_group_increase(websocket, data))
            except json.JSONDecodeError:
                bot_logger.info(f"无效 JSON: {message[:100]}")
            except Exception as e:
                bot_logger.info(f"处理消息时出错: {e}")
    except websockets.exceptions.ConnectionClosed as exc:
        bot_logger.info("")
        if exc.rcvd is None:
            bot_logger.info(divider)
            bot_logger.info(divider_mid + "  💀 连接断开: 僵尸连接被心跳检测发现      " + divider_mid[-1])
            bot_logger.info(divider_mid + "     (TCP 连接已失效，NapCat 客户端未退出)   " + divider_mid[-1])
            bot_logger.info(divider_mid + "     → 请在 NapCat WebUI 中重连，或重启NapCat" + divider_mid[-1])
            bot_logger.info(divider_end)
        else:
            bot_logger.info(divider)
            bot_logger.info(divider_mid + "  🔌 客户端断开连接 (正常断开)              " + divider_mid[-1])
            bot_logger.info(divider_end)
        bot_logger.info("")
    finally:
        tasks = message_queue.disconnect(websocket)
        for task, owner in list(_message_tasks.items()):
            if owner is websocket:
                task.cancel()
                tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)
        if current_websocket is websocket:
            current_websocket = None
            set_napcat_connected(False)


async def main():
    # 初始化命令处理器外部引用
    init_handlers(bot_logger, decision_engine, send_message, send_group_image, send_group_file)

    if PROFILE_ENABLED:
        load_profiles()

    profile_task = asyncio.create_task(profile_update_task())
    mystery_task = asyncio.create_task(mystery_number_task())
    background_tasks = [profile_task, mystery_task]

    async def custom_process_request(path, request_headers):
        return None

    server = await websockets.serve(
        handler, HOST, PORT,
        process_request=custom_process_request,
        ping_interval=30,
        ping_timeout=10
    )
    bot_logger.info(f"机器人服务端 v{VERSION} 启动，监听 {HOST}:{PORT}")
    bot_logger.info("请在 NapCat WebUI 中添加 WebSocket 客户端，地址为：")
    bot_logger.info(f"  ws://{HOST}:{PORT}")
    bot_logger.info("按 Ctrl+C 停止")

    try:
        await asyncio.Future()
    except (asyncio.CancelledError, KeyboardInterrupt):
        bot_logger.info("正在关闭服务...")
    finally:
        server.close()
        await server.wait_closed()
        await message_queue.close()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        bot_logger.info("服务已停止。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        bot_logger.info("\n正在退出...")
