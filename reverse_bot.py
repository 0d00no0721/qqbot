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
import sys
import traceback
import time
from collections import defaultdict, deque
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

# ========== 虚拟股板块 ==========
from scripts.virtual_stock import (
    on_message as vs_on_message,
    init_group_data as vs_init_group,
    start_scheduler as vs_start_scheduler,
    stop_scheduler as vs_stop_scheduler,
    register_group as vs_register_group,
)

# ========== 脚本所在目录 ==========
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

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

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_file_handler.setLevel(logging.INFO)
bot_logger.addHandler(_file_handler)

# ========== 配置 ==========
HOST = "0.0.0.0"
PORT = 8080

# ========== API Key（从 .env 读取） ==========
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")

# ========== 决策引擎专用配置 ==========
DECISION_API_KEY = os.getenv("DECISION_API_KEY", "")
DECISION_API_BASE = "https://api.llm.ustc.edu.cn/v1"

# ========== 发言决策引擎 ==========
decision_engine = DecisionEngine(
    api_key=DECISION_API_KEY,
    api_base_url=DECISION_API_BASE,
    api_model="deepseek-v4-pro",
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


# ========== 消息处理 ==========
async def handle_message(websocket, data: dict):
    t_req = time.time()
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
            processed_msg_ids.add(msg_id)
            if len(processed_msg_ids) > PROCESSED_MSG_MAX:
                processed_msg_ids.clear()

    # 记录已知群
    from scripts.command_handler import known_groups
    known_groups.add(str(group_id))

    # ===== 虚拟股：注册群 + 采集消息指标 =====
    gid_str = str(group_id)
    uid_str_vs = str(user_id)
    try:
        vs_register_group(gid_str)
    except Exception:
        pass

    # ===== 语音消息转文字 =====
    if "[CQ:record" in raw_message:
        raw_message = await process_voice_message(raw_message)

    # 清理 CQ 码，检查 @
    import re
    clean_message = re.sub(r'\[CQ:\w+,[^\]]*\]', '', raw_message).strip()
    bot_mentioned = f"[CQ:at,qq={BOT_QQ}]" in raw_message

    # ===== 纯图片/表情消息忽略 =====
    if not clean_message:
        return

    # ===== 虚拟股：采集消息指标（所有消息，不限@bot） =====
    try:
        vs_on_message(gid_str, uid_str_vs, clean_message, is_bot_command=bot_mentioned)
    except Exception:
        pass

    # ===== 命令处理 =====
    if bot_mentioned:
        handled = await handle_command(websocket, data, clean_message, raw_message)
        if handled:
            return

    # ===== +1 检测 =====
    gid = str(group_id)
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

    # ===== 名字/别名检测 =====
    gid_str = str(group_id)
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

    # ===== 决策引擎 =====
    uid_str = str(user_id)
    t_decision = time.time()
    nickname = data.get("sender", {}).get("nickname", "")
    try:
        decision_engine.add_message(gid_str, uid_str, nickname, clean_or_raw)

        if PROFILE_ENABLED:
            profile_text = get_profile(gid_str, uid_str)
            if profile_text:
                decision_engine.add_message(gid_str, "system", "系统",
                    f"[用户画像: {profile_text}]")

        should_reply, reply_text = await decision_engine.should_reply(
            message=clean_or_raw,
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

    # ===== 更新历史缓存 =====
    group_histories[gid_str].append({
        "content": clean_message or raw_message,
        "user_id": uid_str,
        "timestamp": time.time(),
    })

    # ===== 用户画像：缓冲发言 =====
    if PROFILE_ENABLED:
        record_message(gid_str, uid_str, clean_message or raw_message)


async def handle_group_increase(websocket, data: dict):
    """处理新成员入群通知"""
    group_id = data["group_id"]
    new_user_id = data["user_id"]
    from scripts.command_handler import known_groups
    known_groups.add(str(group_id))
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


async def _safe_handle_message(websocket, data: dict):
    try:
        await handle_message(websocket, data)
    except Exception as e:
        bot_logger.info(f"[task] handle_message 异常: {e}")
        traceback.print_exc()


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
                        asyncio.create_task(_safe_handle_message(websocket, data))
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

    # ===== 虚拟股调度器 =====
    async def vs_broadcast(group_id: str, message: str):
        """虚拟股广播回调：通过当前 WebSocket 发送群消息。"""
        ws = current_websocket
        if ws is None:
            bot_logger.warning(f"[虚拟股] WebSocket 未连接，广播到群 {group_id} 失败")
            return
        try:
            await send_message(ws, int(group_id), message)
        except Exception as e:
            bot_logger.error(f"[虚拟股] 广播失败: {e}")

    vs_start_scheduler(vs_broadcast)
    bot_logger.info("[虚拟股] 调度器已启动（8 个定时任务）")

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
        vs_stop_scheduler()
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        bot_logger.info("服务已停止。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        bot_logger.info("\n正在退出...")