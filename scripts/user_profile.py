"""
用户画像模块
============
基于群聊发言记录，为每个用户构建自然语言画像。
定时批量更新，画像注入决策引擎的 history_messages 中供 AI 参考。

一键禁用: 修改 PROFILE_ENABLED = False 后重启即可。
"""

import asyncio
import json
import os
import sys
import traceback
import logging
from typing import Dict, List, Optional

try:
    from scripts.llm_pool import (
        chat,
        chat_sync,
        available_models,
        get_active_model as _pool_get_active_model,
        LLMPoolExhausted,
    )
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import (
        chat,
        chat_sync,
        available_models,
        get_active_model as _pool_get_active_model,
        LLMPoolExhausted,
    )

# ========== 一键禁用开关 ==========
PROFILE_ENABLED = True

# ========== 日志 ==========
logger = logging.getLogger("user_profile")
logger.setLevel(logging.INFO)
logger.propagate = False

# 控制台输出
_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(logging.Formatter("[画像] %(levelname)s: %(message)s"))
logger.addHandler(_console)

# 文件日志（按启动日期命名）
_SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_SCRIPT_DIR, "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

# 文件日志（按启动日期命名，避免 TimedRotatingFileHandler 在 Windows 下的 os.rename 竞态）
# 注：受限环境下 logs/ 可能整体不可写（本项目已知 ACL 问题），此时退化为"仅控制台日志"，
#     绝不因为日志文件打不开而阻断 import（否则整个机器人都起不来）。
from datetime import datetime as _dt
_LOG_DATE = _dt.now().strftime("%Y-%m-%d")
try:
    _file_handler = logging.FileHandler(
        os.path.join(_LOG_DIR, f"bot.{_LOG_DATE}.log"),
        encoding="utf-8",
    )
    _file_handler.setFormatter(logging.Formatter("[%(asctime)s] [画像] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(_file_handler)
except OSError as _e:
    # 例：PermissionError（logs/ 目录被 ACL 拒绝）——降级为控制台日志
    _console.stream.write(f"[画像] 警告: 文件日志不可用({_e})，已降级为控制台日志\n")

# ========== 配置常量 ==========
PROFILE_UPDATE_INTERVAL = 7200     # 画像更新间隔（秒）— 每2小时一次
PROFILE_MIN_MESSAGES = 5           # 最少发言数才触发更新
PROFILE_MAX_TOKENS = 512           # 画像描述最大 token（曾因 256 不足导致中文截断，扩至 512）
PROFILE_API_TIMEOUT = 40           # API 超时（秒）

# 模型出口：统一走中央模型池脚本（本地网关 http://127.0.0.1:8090/v1），
# 池内按优先级链自动故障转移，不再保留任何云端端点。
# 下列旧名保留仅为兼容既有引用（调用已全部走 llm_pool）。
_API_KEY = os.getenv("LLM_POOL_API_KEY", "")
_API_BASE_URL = ""                       # 出口由池内部决定
_API_MODEL = ""                          # 空 = 用池的 active_model

# 兼容 reverse_bot.py 的 set_fallback_api_key 调用（池自带 key，此值不再参与调用）
_FALLBACK_API_KEY_ENV = "LLM_POOL_API_KEY"
_FALLBACK_API_KEY: Optional[str] = None

# 内容校验参数
_CONTENT_MIN_LENGTH = 10
_CONTENT_MAX_LENGTH = 300
_CONTENT_ERROR_KEYWORDS = [
    "error", "exception", "sorry,", "i cannot", "i am unable",
    "as an ai", "i'm sorry", "i apologize", "unable to",
]

# ========== 全局状态 ==========
_speak_buffer: Dict[str, Dict[str, List[str]]] = {}   # {gid: {uid: [msgs]}}
_profiles: Dict[str, Dict[str, str]] = {}             # {gid: {uid: "自然语言描述"}}
# NapCat 连接状态（由 reverse_bot.py 在 WS 连/断时更新）
_napcat_connected: bool = True
# 受限时段内失败的待重试队列（结构同 _speak_buffer）
_pending_retry: Dict[str, Dict[str, List[str]]] = {}

# ========== 持久化路径 ==========
_PROFILE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "user_profiles.json")


def load_profiles():
    """从 user_profiles.json 读取已有画像到 _profiles。文件不存在则静默跳过。"""
    global _profiles
    if not PROFILE_ENABLED:
        return
    try:
        with open(_PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _profiles = data
                total = sum(len(users) for users in data.values())
                print(f"[画像] 已加载 {total} 个用户画像（{len(data)} 个群）")
    except FileNotFoundError:
        print("[画像] 画像文件不存在，从零开始")
    except json.JSONDecodeError:
        print("[画像] 画像文件损坏，从零开始")


def save_profiles():
    """将 _profiles 写入 user_profiles.json。"""
    try:
        with open(_PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(_profiles, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[画像] 保存失败: {e}")


def set_fallback_api_key(key: str):
    """兼容旧接口：由启动脚本设置备用模型 API Key。

    出口已统一为中央模型池（llm_pool 自行读取 .env 的 LLM_POOL_API_KEY），
    此函数保留仅为不破坏 reverse_bot.py 的既有调用，参数不再参与任何请求。
    """
    global _FALLBACK_API_KEY
    _FALLBACK_API_KEY = key


def set_napcat_connected(connected: bool):
    """设置 NapCat 连接状态（由 reverse_bot.py 在 WS 连/断时调用）。"""
    global _napcat_connected
    _napcat_connected = connected


def _is_school_only_hours() -> bool:
    """判断当前是否在「单次尝试」时段（09:00-12:00 或 14:00-18:00）。

    历史语义为"仅限学校 API"；出口统一为本地模型池后，该时段只表示
    "首选模型返回内容不合格时不做第二次换模型尝试"。
    """
    hour = _dt.now().hour
    return (9 <= hour < 12) or (14 <= hour < 18)


def get_pending_count() -> int:
    """返回待重试队列中的用户数。"""
    return sum(len(users) for users in _pending_retry.values())


def has_pending_retry() -> bool:
    """是否有待重试的画像更新。"""
    return bool(_pending_retry)


def record_message(gid: str, uid: str, text: str):
    """记录一条发言到内存缓冲。PROFILE_ENABLED=False 时直接返回。"""
    if not PROFILE_ENABLED:
        return
    if not text or not text.strip():
        return
    # 单条截断 200 字
    trimmed = text.strip()[:200]
    _speak_buffer.setdefault(gid, {}).setdefault(uid, []).append(trimmed)


def get_profile(gid: str, uid: str) -> Optional[str]:
    """获取用户自然语言画像。禁用或无画像时返回 None。"""
    if not PROFILE_ENABLED:
        return None
    return _profiles.get(gid, {}).get(uid)


async def update_profiles():
    """遍历缓冲中的活跃用户，调用模型池更新画像，保存到文件，清空缓冲。

    在受限时段（09:00-12:00 / 14:00-18:00）只做一次模型尝试，
    失败时根据 NapCat 连接状态分别处理（掉线→等重连 / 其他→入待重试队列+5min重试）。
    """
    if not PROFILE_ENABLED:
        return

    buffer_snapshot = _speak_buffer.copy()
    _speak_buffer.clear()

    if not buffer_snapshot:
        print("[画像] 无新发言，跳过更新")
        return

    school_only = _is_school_only_hours()
    if school_only:
        print(f"[画像] 当前在受限时段（09:00-12:00 / 14:00-18:00），只做一次模型尝试")
    else:
        print(f"[画像] 当前在非受限时段，内容不合格时换池内模型重试")

    total_users = sum(len(users) for users in buffer_snapshot.values())
    print(f"[画像] 开始更新，共 {len(buffer_snapshot)} 个群 {total_users} 个活跃用户")

    updated_count = 0
    for gid, users in buffer_snapshot.items():
        for uid, msgs in users.items():
            if len(msgs) < PROFILE_MIN_MESSAGES:
                # 发言太少，放回缓冲等待积累
                _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)
                continue

            existing = _profiles.get(gid, {}).get(uid)
            try:
                result = await _call_profile_api(msgs, existing, school_only)
                if result:
                    _profiles.setdefault(gid, {})[uid] = result
                    updated_count += 1
                    print(f"[画像] 已更新: 群{gid} 用户{uid} → {result[:50]}...")
                elif school_only:
                    # 受限时段失败
                    if not _napcat_connected:
                        # NapCat 掉线 → 入待重试队列（不塞回 speak_buffer，等重连后重试）
                        _pending_retry.setdefault(gid, {})[uid] = msgs
                        print(f"[画像] 受限时段 + NapCat 掉线，加入待重试队列: 群{gid} 用户{uid}")
                    else:
                        # 其他问题 → 入待重试队列 + 也放回 speak_buffer（5min短周期+下次2h周期双重兜底）
                        _pending_retry.setdefault(gid, {})[uid] = msgs
                        _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)
                        print(f"[画像] 受限时段 API 失败，加入待重试队列+缓冲: 群{gid} 用户{uid}")
                else:
                    # 非受限时段：主备都失败，放回缓冲下次重试
                    _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)
            except Exception as e:
                print(f"[画像] 更新失败 群{gid} 用户{uid}: {e}")
                traceback.print_exc()
                if school_only and not _napcat_connected:
                    _pending_retry.setdefault(gid, {})[uid] = msgs
                else:
                    _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)

    if updated_count > 0:
        save_profiles()
    print(f"[画像] 更新完成，成功 {updated_count} 个")
    if _pending_retry:
        pending_count = sum(len(u) for u in _pending_retry.values())
        print(f"[画像] 待重试: {pending_count} 个用户")


async def retry_pending_updates() -> int:
    """重试待更新队列。

    NapCat 重连后立即触发，或定时更新后 5 分钟短周期触发。
    返回成功更新的用户数。
    """
    global _pending_retry

    if not _pending_retry:
        return 0

    school_only = _is_school_only_hours()
    snapshot = _pending_retry.copy()
    _pending_retry = {}

    updated_count = 0
    still_pending: Dict[str, Dict[str, List[str]]] = {}

    total = sum(len(u) for u in snapshot.values())
    logger.info("开始重试待更新队列，共 %d 个用户（当前%s时段）", total,
                "仅学校 API" if school_only else "非受限")

    for gid, users in snapshot.items():
        for uid, msgs in users.items():
            existing = _profiles.get(gid, {}).get(uid)
            try:
                result = await _call_profile_api(msgs, existing, school_only)
                if result:
                    _profiles.setdefault(gid, {})[uid] = result
                    updated_count += 1
                    logger.info("重试成功: 群%s 用户%s", gid, uid)
                elif school_only and not _napcat_connected:
                    # NapCat 仍然掉线，留在队列继续等待
                    still_pending.setdefault(gid, {})[uid] = msgs
                    logger.info("重试仍失败（NapCat 仍掉线），继续等待: 群%s 用户%s", gid, uid)
                else:
                    # 其他原因 → 放回 speak_buffer 等下次 2h 周期
                    _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)
                    logger.info("重试失败，放回缓冲等下次周期: 群%s 用户%s", gid, uid)
            except Exception as e:
                logger.error("重试异常 群%s 用户%s: %s", gid, uid, e)
                traceback.print_exc()
                if school_only and not _napcat_connected:
                    still_pending.setdefault(gid, {})[uid] = msgs
                else:
                    _speak_buffer.setdefault(gid, {}).setdefault(uid, []).extend(msgs)

    _pending_retry = still_pending

    if updated_count > 0:
        save_profiles()

    remaining = sum(len(u) for u in _pending_retry.values())
    logger.info("重试完成，成功 %d 个，仍在等待 %d 个", updated_count, remaining)
    return updated_count


def _validate_profile(text: Optional[str]) -> bool:
    """校验画像文本是否有效。返回 True=有效。"""
    if not text or not text.strip():
        return False
    text_stripped = text.strip()
    if len(text_stripped) < _CONTENT_MIN_LENGTH:
        return False
    if len(text_stripped) > _CONTENT_MAX_LENGTH:
        return False
    text_lower = text_stripped.lower()
    for kw in _CONTENT_ERROR_KEYWORDS:
        if kw.lower() in text_lower:
            return False
    # 注：之前有标点结尾校验（'。！？」）)'），已移除——它拒绝了很多有效内容
    # 剩余的空/长度/关键词校验已足够保护画像质量
    return True


async def _call_single_api(api_key: str, api_base_url: str, api_model: str,
                           prompt: str, *, preferred: Optional[str] = None) -> Optional[str]:
    """调用中央模型池生成画像。返回文本，失败返回 None。

    api_key / api_base_url / api_model 为历史签名保留参数（调用方仍按旧样式传），
    实际出口已统一为 scripts/llm_pool.py 的本地网关模型池，三者不再使用。
    preferred 指定首试模型（留空 = 池的 active_model；不在池中时自动忽略）。
    """
    try:
        text, _model = await chat(
            [{"role": "user", "content": prompt}],
            label="profile",
            max_tokens=PROFILE_MAX_TOKENS,
            timeout=float(PROFILE_API_TIMEOUT),
            preferred=preferred or None,
        )
        return text.strip() if text and text.strip() else None
    except LLMPoolExhausted as e:
        logger.warning("模型池已耗尽: %s", e)
        return None
    except Exception as e:
        logger.error("模型池调用失败: %s", e)
        return None


def _alternate_pool_model() -> str:
    """取池内与当前 active_model 不同的第一个模型，作为内容不合格时的"备用"。

    池内取不到时返回空串（此时 _call_single_api 会走池的默认顺序）。
    """
    try:
        models = available_models()
    except Exception:
        return ""
    if not models:
        return ""
    try:
        active = str(_pool_get_active_model() or "").strip()
    except Exception:
        active = ""
    for model in models:
        if model != active:
            return model
    return models[0]


async def _call_profile_api(msgs: List[str], existing: Optional[str],
                            school_only: bool = False) -> Optional[str]:
    """调用模型池生成/更新用户画像。

    Args:
        msgs: 用户近期发言列表
        existing: 已有画像（首次为 None）
        school_only: True=受限时段，只做一次模型尝试；
                     False=内容不合格时换池内另一个模型再试一次

    Returns:
        画像文本，失败返回 None。
    """
    recent = "\n".join(f"- {m}" for m in msgs[-20:])  # 最多取最近 20 条

    if existing:
        prompt = (
            f"已有画像：{existing}\n\n"
            f"该用户最近新发言：\n{recent}\n\n"
            f"请结合新发言更新画像。保持第三人称、50-100字，包含风格/兴趣/情绪特征。"
            f"直接输出更新后的画像文本，不要加任何前缀或解释。"
        )
    else:
        prompt = (
            f"你是用户画像分析师。根据以下QQ群发言记录，提炼该用户的特征画像：\n\n"
            f"{recent}\n\n"
            f"要求：第三人称、50-100字，包含说话风格/兴趣爱好/情绪特征。"
            f"直接输出画像文本，不要加任何前缀或解释。"
        )

    # Step 1: 尝试池内首选模型（active_model，池内部自带降档）
    result = await _call_single_api(_API_KEY, _API_BASE_URL, _API_MODEL, prompt)
    if result and _validate_profile(result):
        return result

    if school_only:
        # 受限时段：不做第二次尝试（保留历史行为，避免额外消耗）
        if result and not _validate_profile(result):
            logger.warning("首选模型画像内容异常，受限时段不做第二次尝试")
        else:
            logger.warning("首选模型调用失败，受限时段不做第二次尝试")
        return None

    if result and not _validate_profile(result):
        logger.warning("首选模型画像内容异常（含错误关键词/长度异常），切换池内备用模型")
    else:
        logger.warning("首选模型调用失败或无返回，切换池内备用模型")

    # Step 2: 换池内另一个模型再试一次（内容不合格时换模型比原样重试更有效）
    fb_model = _alternate_pool_model()
    if fb_model:
        logger.info("调用池内备用模型 %s", fb_model)
    else:
        logger.warning("池内无可切换的备用模型，按池默认顺序重试")
    fb_result = await _call_single_api(
        "", "", fb_model, prompt, preferred=fb_model or None
    )
    if fb_result and _validate_profile(fb_result):
        logger.info("备用模型画像生成成功")
        return fb_result

    if result:
        # 备用也失败，返回首选模型结果（容忍无效内容，总比没有好）
        logger.warning("备用模型也失败，回退使用首选模型结果")
        return result

    logger.error("两模型均失败，画像生成失败")
    return None