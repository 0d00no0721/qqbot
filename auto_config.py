#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动配置 NapCat + NoneBot2 反向 WebSocket 连接
"""

import os
import sys
import json
import subprocess
import time

# 路径配置
QQBOT_DIR = r"E:\QQbot"
ENV_FILE = os.path.join(QQBOT_DIR, "qqbot", ".env")
NAPCAT_API = "http://127.0.0.1:6099/api"  # NapCat HTTP API 地址

def print_step(msg):
    print(f"\n[STEP] {msg}")

def print_ok(msg):
    print(f"[OK]   {msg}")

def print_error(msg):
    print(f"[ERR]  {msg}")

def install_requests():
    """安装 requests 库（如果未安装）"""
    try:
        import requests
        return True
    except ImportError:
        print_step("安装 requests 库...")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
        print_ok("requests 安装完成")
        return True

def configure_env():
    """修改 .env 文件为反向 WebSocket 模式"""
    print_step("配置 NoneBot2 环境变量 (.env)")
    env_content = """ENVIRONMENT=dev
DRIVER=~fastapi
HOST=127.0.0.1
PORT=8080
"""
    try:
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(env_content)
        print_ok(f"已写入 {ENV_FILE}")
    except Exception as e:
        print_error(f"写入 .env 失败: {e}")
        return False
    return True

def get_napcat_token():
    """从 NapCat 命令行窗口获取 token（简单方法：假设固定 token）"""
    # 实际 token 每次可能不同，但通常 NapCat 允许从 API 获取 token
    # 这里我们尝试从本地文件读取，或者让用户输入
    config_dir = os.path.join(QQBOT_DIR, "NapCat", "NapCat.44498.Shell", "versions", "9.9.26-44498", "resources", "app", "napcat", "config")
    token_file = os.path.join(config_dir, "napcat_2668851638.json")  # 替换为你的QQ号
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                token = data.get("webui_token", "")
                if token:
                    return token
        except:
            pass
    # 如果找不到，提示用户从控制台复制
    print("请从 NapCat 命令行窗口中找到类似 WebUi Token: xxxxx 的字符串，输入 token：")
    token = input("Token: ").strip()
    return token

def add_websocket_client_via_api(token):
    """通过 NapCat API 添加 WebSocket 客户端配置"""
    import requests
    print_step("通过 API 添加 WebSocket 客户端配置")
    
    # 获取当前配置
    url_list = f"{NAPCAT_API}/config"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        resp = requests.get(url_list, headers=headers, timeout=5)
        if resp.status_code != 200:
            print_error(f"获取配置失败: {resp.text}")
            return False
        configs = resp.json()
    except Exception as e:
        print_error(f"连接 NapCat API 失败: {e}")
        return False
    
    # 删除已有的 WebSocket 客户端（如果有）
    modified = False
    for idx, cfg in enumerate(configs.get("configs", [])):
        if cfg.get("type") == "WebSocket Client":
            print_step(f"删除已有的 WebSocket 客户端配置: {cfg.get('name', '未命名')}")
            del_resp = requests.delete(f"{url_list}/{cfg.get('id')}", headers=headers)
            if del_resp.status_code == 200:
                print_ok("删除成功")
                modified = True
    
    # 添加新的 WebSocket 客户端
    new_config = {
        "type": "WebSocket Client",
        "name": "Connect-to-NoneBot2",
        "address": "ws://127.0.0.1:8080/onebot/v11/ws",
        "access_token": "",
        "enabled": True
    }
    add_resp = requests.post(url_list, headers=headers, json=new_config)
    if add_resp.status_code in (200, 201):
        print_ok("WebSocket 客户端添加成功")
        return True
    else:
        print_error(f"添加失败: {add_resp.text}")
        return False

def manual_webui_hint():
    """如果 API 失败，提示用户手动配置"""
    print("\n[提示] 无法自动配置 NapCat，请手动完成以下步骤：")
    print("1. 打开浏览器访问 http://127.0.0.1:6099/webui (需要 token，见 NapCat 窗口)")
    print("2. 登录（默认 napcat / napcat）")
    print("3. 进入「网络配置」，删除已有的 WebSocket 客户端（如有）")
    print("4. 新建「WebSocket 客户端」，地址填入：")
    print("   ws://127.0.0.1:8080/onebot/v11/ws")
    print("5. 保存并启用")
    input("\n完成后按回车继续...")

def restart_nonebot2():
    """重启 NoneBot2（用户手动）"""
    print_step("请手动重启 NoneBot2")
    print("在终端中执行以下命令：")
    print(f"  cd {os.path.join(QQBOT_DIR, 'qqbot')}")
    print("  python bot.py")
    input("按回车键退出脚本...")

def main():
    print("=== NapCat + NoneBot2 自动配置工具 ===")
    if not configure_env():
        return
    install_requests()
    
    # 尝试自动获取 token
    token = get_napcat_token()
    if token:
        print_ok(f"获取到 token: {token[:8]}...")
        if not add_websocket_client_via_api(token):
            manual_webui_hint()
    else:
        manual_webui_hint()
    
    restart_nonebot2()

if __name__ == "__main__":
    main()