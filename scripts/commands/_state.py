# -*- coding: utf-8 -*-
"""
共享状态层 — 集中存放被多模块访问的全局状态、常量、锁。
子模块统一通过 ``from . import _state`` 后访问属性（避免值导入
看不到 init_handlers 的重赋值）。
"""

import asyncio
import os
from collections import defaultdict
from typing import Dict, Set

# ========== 脚本所在目录（项目根） ==========
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 配置常量 ==========
BOT_QQ = "2668851638"
DATA_FILE = os.path.join(SCRIPT_DIR, "menu_data.json")

VERSION = "2.3.2"

# DeepSeek（Yau / 算卦 / 记忆分类 / 模型测试 共用）
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# Yau 限流参数
YAU_MAX_TOKENS = 150
YAU_TIMEOUT = 15
YAU_MAX_USAGE = 3
YAU_WINDOW = 3600

# 算卦
DIVINATION_API_KEY = os.getenv("DIVINATION_API_KEY", "")
DIVINATION_MODEL = "deepseek-chat"
DIVINATION_MAX_TOKENS = 150

# 周礼限流参数
ZHOULI_MAX_LENGTH = 300
ZHOULI_RATE_LIMIT = 3
ZHOULI_RATE_WINDOW = 3600

# NapCat HTTP API
NAPCAT_HTTP_URL = "http://127.0.0.1:6099"
VOICE_TRANSCRIBE_TIMEOUT = 10

# 像素画
PIXEL_SIZE = 32
PREVIEW_SCALE = 10
TEMP_IMAGE_PATH = os.path.join(SCRIPT_DIR, "temp_input.jpg")
TEMP_PREVIEW_PATH = os.path.join(SCRIPT_DIR, "preview.png")
TEMP_TEX_PATH = os.path.join(SCRIPT_DIR, "pixel_output.tex")

# ========== 并发安全锁 ==========
_dedup_lock = asyncio.Lock()
_yau_lock = asyncio.Lock()
_menu_lock = asyncio.Lock()
_sign_in_lock = asyncio.Lock()
_zhouli_lock = asyncio.Lock()

# 神秘数字等待队列（每 5s 处理一个请求）
_mystery_queue = asyncio.Queue()

# ========== 频率限制记录 ==========
yau_usage: Dict[str, list] = defaultdict(list)
zhouli_usage: Dict[str, list] = defaultdict(list)

# ========== 消息去重 ==========
processed_msg_ids: Set[int] = set()
PROCESSED_MSG_MAX = 200

# ========== 运行时状态 ==========
known_groups: set = set()

# ========== 外部依赖（由 init_handlers 注入） ==========
bot_logger = None
decision_engine = None
send_message_fn = None
send_group_image_fn = None
send_group_file_fn = None


def init_handlers(logger, de, send_msg_fn, send_img_fn, send_file_fn):
    """初始化外部引用（reverse_bot.py 在 main() 中调用一次）"""
    global bot_logger, decision_engine, send_message_fn, send_group_image_fn, send_group_file_fn
    bot_logger = logger
    decision_engine = de
    send_message_fn = send_msg_fn
    send_group_image_fn = send_img_fn
    send_group_file_fn = send_file_fn
