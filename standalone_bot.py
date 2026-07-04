#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立 QQ 群机器人 - 今天吃什么
通过 WebSocket 连接 NapCat 服务端，支持心跳和鉴权
"""

import asyncio
import json
import os
import random
import re
import signal
import sys
from typing import Dict, List

import websockets

# ========== 配置 ==========
NAPCAT_WS_URL = "ws://127.0.0.1:6700"
DATA_FILE = "menu_data.json"
RECONNECT_DELAY = 5
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）

# ========== 数据操作 ==========
def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_dish(group_id: str, user_id: str, dish: str) -> bool:
    data = load_data()
    group_data = data.setdefault(group_id, {})
    user_menu = group_data.setdefault(user_id, [])
    if dish in user_menu:
        return False
    user_menu.append(dish)
    save_data(data)
    return True

def get_personal_menu(group_id: str, user_id: str) -> List[str]:
    data = load_data()
    return data.get(group_id, {}).get(user_id, [])

def get_all_dishes(group_id: str) -> List[tuple]:
    data = load_data()
    group_data = data.get(group_id, {})
    items = []
    for uid, dishes in group_data.items():
        for dish in dishes:
            items.append((dish, uid))
    return items

# ========== 消息处理 ==========
async def send_message(websocket, group_id: int, message: str):
    payload = {
        "action": "send_group_msg",
        "params": {
            "group_id": group_id,
            "message": message
        }
    }
    await websocket.send(json.dumps(payload))

async def handle_message(websocket, data: dict):
    if data.get("post_type") != "message":
        return
    if data.get("message_type") != "group":
        return
    group_id = data["group_id"]
    user_id = data["user_id"]
    raw_message = data["raw_message"].strip()
    if data.get("self_id") == user_id:
        return

    match_add = re.match(r"^(添加菜单|加菜)\s+(.+)$", raw_message)
    if match_add:
        dish = match_add.group(2).strip()
        if add_dish(str(group_id), str(user_id), dish):
            reply = f"成功添加「{dish}」到你的菜单~"
        else:
            reply = f"你已经添加过「{dish}」了！"
        await send_message(websocket, group_id, reply)
        return

    if raw_message in ("今天吃啥 自己", "我自己吃啥"):
        menu = get_personal_menu(str(group_id), str(user_id))
        if not menu:
            reply = "你还没有添加任何菜品，先输入“添加菜单 菜名”吧~"
        else:
            chosen = random.choice(menu)
            reply = f"今天你自己吃：{chosen}"
        await send_message(websocket, group_id, reply)
        return

    if raw_message in ("今天吃啥 群", "群里吃啥"):
        items = get_all_dishes(str(group_id))
        if not items:
            reply = "群里还没有任何菜品，大家快用“添加菜单”来加菜吧~"
            await send_message(websocket, group_id, reply)
            return
        chosen_dish, provider_uid = random.choice(items)
        reply = f"今天全群吃：{chosen_dish}（由 {provider_uid} 提供）"
        await send_message(websocket, group_id, reply)
        return

# ========== WebSocket 心跳 ==========
async def heartbeat(websocket):
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        try:
            # 发送心跳包（NapCat 可能要求 ping 帧或者特定 JSON）
            pong_waiter = await websocket.ping()
            await pong_waiter
        except:
            break

# ========== 主连接逻辑 ==========
async def listen():
    while True:
        try:
            async with websockets.connect(NAPCAT_WS_URL, ping_interval=HEARTBEAT_INTERVAL, ping_timeout=30) as websocket:
                print(f"✅ 已连接到 NapCat WebSocket 服务端 ({NAPCAT_WS_URL})")
                # 发送初始化消息（根据 NapCat 要求，可选）
                try:
                    init_msg = json.dumps({"action": "get_version", "params": {}})
                    await websocket.send(init_msg)
                    await asyncio.sleep(0.5)
                except:
                    pass

                # 接收消息循环
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        # 处理心跳回应或事件
                        if data.get("post_type"):
                            await handle_message(websocket, data)
                        # 其他可能的响应忽略
                    except json.JSONDecodeError:
                        print(f"⚠️ 无效 JSON: {message[:100]}")
                    except Exception as e:
                        print(f"❌ 处理消息时出错: {e}")
        except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError) as e:
            print(f"⚠️ 连接断开: {e}，{RECONNECT_DELAY} 秒后重连...")
            await asyncio.sleep(RECONNECT_DELAY)
        except Exception as e:
            print(f"❌ 未知错误: {e}，{RECONNECT_DELAY} 秒后重试...")
            await asyncio.sleep(RECONNECT_DELAY)

def shutdown(sig, frame):
    print("\n🛑 正在退出...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print("🤖 独立机器人启动中...")
    print(f"📡 连接地址: {NAPCAT_WS_URL}")
    print("📁 数据文件: menu_data.json")
    print("按 Ctrl+C 退出\n")
    asyncio.run(listen())