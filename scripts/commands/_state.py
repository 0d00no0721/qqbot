# -*- coding: utf-8 -*-
"""
共享状态层 — 集中存放被多模块访问的全局状态、常量、锁。
子模块统一通过 ``from . import _state`` 后访问属性（避免值导入
看不到 init_handlers 的重赋值）。
"""

import asyncio
import os
import threading
import time
from collections import defaultdict
from typing import Dict, Set

# ========== 脚本所在目录（项目根） ==========
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 配置常量 ==========
BOT_QQ = "2668851638"
DATA_FILE = os.path.join(SCRIPT_DIR, "menu_data.json")

VERSION = "2.5.0"

# ========== 模型出口：中央模型池（本地网关） ==========
# 全项目唯一的模型出口是 scripts/llm_pool.py，其 base_url 来自
# config/model_pool.json / .env，默认 http://127.0.0.1:8090/v1。
# 这里只放"常量层"的只读快照，供仍然 import 这些旧名字的模块使用；
# 所有实际调用请直接走 llm_pool.chat / chat_sync。
try:
    from scripts.llm_pool import (
        chat,
        chat_sync,
        available_models as _pool_available_models,
        load_config as _pool_load_config,
        get_active_model as _pool_get_active_model,
        LLMPoolExhausted,
    )
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import (
        chat,
        chat_sync,
        available_models as _pool_available_models,
        load_config as _pool_load_config,
        get_active_model as _pool_get_active_model,
        LLMPoolExhausted,
    )

# 网关地址兜底（llm_pool 读不到配置时使用，保证常量永远可用）
_POOL_BASE_URL = "http://127.0.0.1:8090/v1"
_POOL_API_KEY = os.getenv("LLM_POOL_API_KEY", "")
_POOL_MODELS: list = []
try:
    _pool_cfg = _pool_load_config()
    _POOL_BASE_URL = str(_pool_cfg.get("base_url") or _POOL_BASE_URL).rstrip("/") or _POOL_BASE_URL
    _POOL_API_KEY = str(_pool_cfg.get("api_key") or _POOL_API_KEY)
    _POOL_MODELS = list(_pool_available_models())
except Exception:  # pragma: no cover - 常量层绝不因配置问题阻断 import
    _POOL_MODELS = []

# 池内当前生效的模型（active_model 必须在池里，否则用优先级链首档）
_pool_active = ""
try:
    _pool_active = str(_pool_get_active_model() or "").strip()
except Exception:  # pragma: no cover
    _pool_active = ""
if _pool_active and _pool_active in _POOL_MODELS:
    _POOL_DEFAULT_MODEL = _pool_active
elif _POOL_MODELS:
    _POOL_DEFAULT_MODEL = _POOL_MODELS[0]
else:
    _POOL_DEFAULT_MODEL = "deepseek-flash"

# DeepSeek 旧名（Yau / 算卦 / 记忆分类 / 模型测试 共用）——保留名字兼容旧引用，
# 语义已改为"本地网关 + 池内模型"，不再是云端端点。
DEEPSEEK_API_KEY = _POOL_API_KEY
DEEPSEEK_BASE_URL = _POOL_BASE_URL[:-3] if _POOL_BASE_URL.endswith("/v1") else _POOL_BASE_URL
DEEPSEEK_API_BASE = _POOL_BASE_URL
DEEPSEEK_MODEL = _POOL_DEFAULT_MODEL

# Yau 限流参数
YAU_MAX_TOKENS = 150
YAU_TIMEOUT = 15
YAU_MAX_USAGE = 3
YAU_WINDOW = 3600

# 算卦（键名保留兼容；实际调用走模型池，key 由池内部提供）
DIVINATION_API_KEY = _POOL_API_KEY
DIVINATION_MODEL = _POOL_DEFAULT_MODEL
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

# 识图（vision）限流：滑动窗口计数，window 与上限都可由 reverse_bot 传入
_vision_lock = threading.Lock()
vision_usage: Dict[str, list] = defaultdict(list)


def vision_rate_ok(gid: str, limit: int = 10, window: float = 3600.0) -> tuple:
    """识图滑动窗口限流：`(是否放行, 窗口内已用张数)`。

    - 只记录**调用时刻**，不做任何 I/O；窗口外的旧记录顺手清掉；
    - 放行时追加一条当前时间戳（调用点必须"先问后识图"，问过没识也算占额，
      宁可少识不可无限识）；
    - `limit <= 0` → 一律拒绝（把开关当硬闸）。
    """
    gid = str(gid or "")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 0
    now = time.time()
    with _vision_lock:
        buf = vision_usage[gid]
        cutoff = now - float(window)
        while buf and buf[0] < cutoff:
            buf.pop(0)
        used = len(buf)
        if limit <= 0 or used >= limit:
            return False, used
        buf.append(now)
        return True, used + 1

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
