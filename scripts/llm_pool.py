# -*- coding: utf-8 -*-
"""
中央模型池（LLM Pool）—— 机器人唯一的模型出口。

设计要点：
- 所有模型调用统一走本地网关 http://127.0.0.1:8090/v1（不保留云端兜底）。
- 按优先级链 tier 依次尝试；档内按列表顺序试；某档全挂才降档。
- preferred（config/active_model.json 的 active_model）优先后置于链首，只试一次。
- 失败判定（按 PROBE.md 实测的错误形态分流，四类处置）：
    · auth（401 / 鉴权语义 403）→ **立即中止整池**，不设冷却，抛 LLMPoolAuthError；
    · request（参数/校验类 400，如 temperature 非法、messages 为空）→ **不设冷却**、换下一个；
      若本次尝试全部是请求侧错误 → 抛 LLMPoolRequestError（请求有问题，不是模型故障）；
    · model_missing（404 / 400 invalid model name 等）→ 长冷却(600s)；
    · transient（连接错误/超时/429/5xx/200 空内容/解析失败）→ 普通冷却(60s)。
  冷却中的模型直接跳过。
- max_tokens 下限：config 的 min_max_tokens（默认 512）是安全网，可按模型用
  model_overrides 抬高；T3/T4/T5 是"先思考后回答"型，max_tokens 给小了会
  HTTP 200 + content 空 → 会被误判成模型故障（PROBE R1），故统一取
  max(调用方值, 该模型下限)。max_tokens_default=500 因此实际生效为 512。
- per-model 能力开关：no_response_format 里的模型不发 response_format
  （qwen3.8-flash-next 会把 JSON 落进 reasoning_content、content=null，PROBE R3）。
- 审计：每次尝试与最终结果追加一行 JSON 到 logs/llm_pool.jsonl，超 5MB 轮转为 .1；
  err 字段只落 `HTTP <status>/<error.type>: <message 截断 200 字>`，绝不写整段响应体
  （404 body 会回显上游多 key 的内部名字，PROBE R5）。
- 软开关：LLM_POOL_DISABLE=1 时只调 active_model 单模型（不降档），便于定位
  "是不是池的问题"。

依赖：仅标准库 + httpx（项目已有）。
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

# scripts/llm_pool.py -> 项目根 = 上上级
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "model_pool.json"
ACTIVE_MODEL_PATH = PROJECT_ROOT / "config" / "active_model.json"
LOG_PATH = PROJECT_ROOT / "logs" / "llm_pool.jsonl"
# 备用审计位置：受限沙箱下 logs/ 整目录拒绝子进程写入时使用
FALLBACK_LOG_PATH = (
    PROJECT_ROOT / "qq_native_re" / "findings" / "model_pool" / "llm_pool.jsonl"
)
LOG_MAX_BYTES = 5 * 1024 * 1024

# 自己加载 .env：本项目已知坑是子进程脚本（model_primary/model_fallback）不调用
# load_dotenv，只靠继承父进程环境。llm_pool 可能被独立运行/被那些子进程 import，
# 这里补一次（override=False，父进程已有的环境变量优先，不会覆盖线上配置）。
try:  # pragma: no cover - 环境准备
    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(PROJECT_ROOT / ".env", override=False)
except Exception:  # pragma: no cover - dotenv 缺失也不影响主流程
    pass

DEFAULT_BASE_URL = "http://127.0.0.1:8090/v1"
DEFAULT_MAX_TOKENS = 500
# 思考型模型（T3/T4/T5）在小 max_tokens 下会 HTTP 200 + content 空（PROBE R1）。
# 512 是覆盖所有思考型最低需求（glm 128 / 其余 64）并留 4–8× 余量的安全网。
DEFAULT_MIN_MAX_TOKENS = 512
DEFAULT_ATTEMPT_TIMEOUT = 20.0
DEFAULT_TOTAL_BUDGET = 45.0
DEFAULT_COOLDOWN = 60.0
DEFAULT_HARD_COOLDOWN = 600.0
# 视觉白名单兜底（config/model_pool.json 的 vision_models 缺失/为空时用）。
# 依据：实测 deepseek-flash / glm-5.3-flash / qwen3.8-chat 能正确识图；
# qwen3.8-flash-next 对图片只回 reasoning_content、正文为空（会被池判为空内容）；
# unlimited-ocr 是 OCR 模型，不是视觉模型。
DEFAULT_VISION_MODELS = ("deepseek-flash", "glm-5.3-flash", "qwen3.8-chat")

# 判定"模型不存在/不支持"的关键词（用于区分长冷却与普通冷却）
_MODEL_MISSING_MARKERS = (
    "model not found",
    "no such model",
    "unknown model",
    "model_not_found",
    "model does not exist",
    "model not exist",
    "does not support",
    "unsupported model",
    "not a valid model",
    "invalid model",
    "模型不存在",
    "模型不存在或",
    "不支持的模型",
    "无此模型",
)

# 请求侧问题（参数/校验类 400）：对池内任何模型都会一模一样地失败，
# 绝不能当成"模型故障"冷却（PROBE R4：一次坏请求会冻死整池）。
_REQUEST_SIDE_TYPES = frozenset(
    {
        "litellm.badrequesterror",
        "bad_request_error",
        "invalid_request_params",
        "invalid_request_params_error",
    }
)
_REQUEST_SIDE_MARKERS = (
    "badrequesterror",
    "failed to deserialize",
    "validation:",
    "validation error",
    "unsupported parameter",
    "cannot be empty",
    "invalid type:",
)
# 鉴权类错误（全局故障，重试无意义，立即止损；PROBE §4.4）
_AUTH_ERROR_TYPES = frozenset(
    {
        "auth_error",
        "authentication_error",
        "invalid_api_key",
        "invalid_token",
        "permission_error",
        "permission_denied",
        "unauthorized",
    }
)
_AUTH_MARKERS = (
    "invalid token",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "authentication failed",
    "鉴权失败",
    "无效的令牌",
    "密钥无效",
)
# 403 里"这个 key 不能访问某个模型"是**按模型**的能力限制（PROBE §4.1 的成因），
# 语义等于"模型不可用"，不是全局鉴权故障 —— 不能因此中止整池。
_MODEL_SCOPED_MARKERS = (
    "not allowed to access model",
    "can only access models",
    "no permission to access model",
    "cannot access model",
)

# 审计脱敏：任何形如 sk-xxxx 的串一律打码，绝不把 key 写进日志
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}", re.IGNORECASE)
# 审计 err 字段里 message 的截断长度（PROBE R5）
_ERR_MESSAGE_LIMIT = 200


class LLMPoolExhausted(RuntimeError):
    """池内所有模型都失败时抛出，消息里带每个模型的失败原因摘要。"""


class LLMPoolRequestError(LLMPoolExhausted):
    """请求本身有问题（参数/校验类错误），不是模型故障。

    池内每个模型都会以同样方式失败，故不做冷却、不认定模型故障。
    继承 LLMPoolExhausted，确保下游 `except LLMPoolExhausted` 不会漏捕获。
    """


class LLMPoolAuthError(LLMPoolExhausted):
    """网关鉴权失败（401 / 鉴权语义 403）——全局故障，立即中止整池尝试。

    继承 LLMPoolExhausted，确保下游 `except LLMPoolExhausted` 不会漏捕获。
    """


# ---------------------------------------------------------------------------
# 模块级状态：配置缓存 / 冷却表 / 锁
# ---------------------------------------------------------------------------

_CONFIG_CACHE: Optional[dict] = None
_CONFIG_MTIME: Optional[float] = None
_CONFIG_LOCK = threading.RLock()

# model -> 冷却截止的 monotonic 时间戳
_COOLDOWN: dict[str, float] = {}
_COOLDOWN_LOCK = threading.RLock()

# 审计日志锁定（多线程/多事件循环共用）
_LOG_LOCK = threading.RLock()

# 实际可写的审计日志路径（首次写入时探测，之后缓存）
_LOG_RESOLVED: Optional[Path] = None


# ---------------------------------------------------------------------------
# 内置兜底配置（配置文件丢失时使用，保持与 config/model_pool.json 一致）
# ---------------------------------------------------------------------------

_FALLBACK_CONFIG = {
    "base_url_env": "LLM_POOL_BASE_URL",
    "api_key_env": "LLM_POOL_API_KEY",
    "tiers": [
        ["deepseek-flash", "deepseek-flash-2"],
        ["deepseek-v4-flash-ascend", "deepseek-v4-flash-ascend1"],
        ["qwen3.8-flash-next-2", "qwen3.8-flash-next"],
        ["glm-5.3-flash"],
        ["qwen3.8-reasoner"],
    ],
    "attempt_timeout_seconds": DEFAULT_ATTEMPT_TIMEOUT,
    "total_budget_seconds": DEFAULT_TOTAL_BUDGET,
    "cooldown_seconds": DEFAULT_COOLDOWN,
    "hard_cooldown_seconds": DEFAULT_HARD_COOLDOWN,
    "max_tokens_default": DEFAULT_MAX_TOKENS,
    "min_max_tokens": DEFAULT_MIN_MAX_TOKENS,
    # 结构可扩展：后续可按模型加别的键（如 extra_params / timeout_seconds）
    "model_overrides": {
        "glm-5.3-flash": {"min_max_tokens": 128},
    },
    "no_response_format": ["qwen3.8-flash-next"],
    # 视觉白名单（vision=True 时只在这些模型里挑；留空则回退全链）
    "vision_models": list(DEFAULT_VISION_MODELS),
}


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def _soft_disabled() -> bool:
    """软开关：LLM_POOL_DISABLE=1 时只调 active_model 单模型，不降档。"""
    return str(os.environ.get("LLM_POOL_DISABLE", "")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _load_config() -> dict:
    """读 config/model_pool.json（mtime 缓存，改了文件无需重启进程）。

    base_url/api_key 优先取 json 里的直写值，缺省则取环境变量
    （变量名由 json 的 base_url_env / api_key_env 指定，默认
    LLM_POOL_BASE_URL / LLM_POOL_API_KEY）。
    配置文件缺失或损坏时退化为内置默认值，保证调用点不至于直接崩。
    """
    global _CONFIG_CACHE, _CONFIG_MTIME

    with _CONFIG_LOCK:
        raw: Optional[dict] = None
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            mtime = None

        if mtime is not None:
            if _CONFIG_CACHE is not None and _CONFIG_MTIME == mtime:
                return _CONFIG_CACHE
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
            if raw is not None:
                _CONFIG_MTIME = mtime

        if raw is None:
            # 文件缺失/解析失败：沿用已有缓存，否则用内置兜底
            if _CONFIG_CACHE is not None:
                return _CONFIG_CACHE
            raw = dict(_FALLBACK_CONFIG)

        cfg = dict(raw)

        # 归一化 tiers：只保留非空的模型名列表
        tiers: list[list[str]] = []
        for tier in cfg.get("tiers") or []:
            if isinstance(tier, str):
                tier = [tier]
            names = [str(m).strip() for m in (tier or []) if str(m).strip()]
            if names:
                tiers.append(names)
        if not tiers:
            tiers = [list(t) for t in _FALLBACK_CONFIG["tiers"]]
        cfg["tiers"] = tiers

        # base_url / api_key：json 直写优先，其次环境变量
        base_env = str(cfg.get("base_url_env") or "LLM_POOL_BASE_URL")
        key_env = str(cfg.get("api_key_env") or "LLM_POOL_API_KEY")
        cfg["base_url"] = (
            str(cfg.get("base_url") or "").strip()
            or os.environ.get(base_env, "").strip()
            or DEFAULT_BASE_URL
        )
        cfg["api_key"] = (
            str(cfg.get("api_key") or "").strip() or os.environ.get(key_env, "").strip()
        )
        cfg["base_url_env"] = base_env
        cfg["api_key_env"] = key_env

        # 数值参数兜底（容忍 json 里写成字符串）
        def _num(key: str, default: float) -> float:
            try:
                val = float(cfg.get(key, default))
            except (TypeError, ValueError):
                return float(default)
            return val if val > 0 else float(default)

        cfg["attempt_timeout_seconds"] = _num(
            "attempt_timeout_seconds", DEFAULT_ATTEMPT_TIMEOUT
        )
        cfg["total_budget_seconds"] = _num(
            "total_budget_seconds", DEFAULT_TOTAL_BUDGET
        )
        cfg["cooldown_seconds"] = _num("cooldown_seconds", DEFAULT_COOLDOWN)
        cfg["hard_cooldown_seconds"] = _num(
            "hard_cooldown_seconds", DEFAULT_HARD_COOLDOWN
        )
        try:
            mt = int(cfg.get("max_tokens_default", DEFAULT_MAX_TOKENS))
        except (TypeError, ValueError):
            mt = DEFAULT_MAX_TOKENS
        cfg["max_tokens_default"] = mt if mt > 0 else DEFAULT_MAX_TOKENS

        # 思考型模型的 max_tokens 下限（安全网），以及 per-model 覆盖
        try:
            floor = int(cfg.get("min_max_tokens", DEFAULT_MIN_MAX_TOKENS))
        except (TypeError, ValueError):
            floor = DEFAULT_MIN_MAX_TOKENS
        cfg["min_max_tokens"] = floor if floor > 0 else DEFAULT_MIN_MAX_TOKENS

        overrides: dict[str, dict] = {}
        raw_overrides = cfg.get("model_overrides")
        if isinstance(raw_overrides, dict):
            for name, spec in raw_overrides.items():
                model_name = str(name).strip()
                if not model_name or not isinstance(spec, dict):
                    continue
                item: dict[str, Any] = {}
                try:
                    sub = int(spec.get("min_max_tokens"))
                except (TypeError, ValueError):
                    sub = 0
                if sub > 0:
                    item["min_max_tokens"] = sub
                if item:
                    overrides[model_name] = item
        cfg["model_overrides"] = overrides

        # 不吃 response_format 的模型（PROBE R3：qwen3.8-flash-next 会把 JSON
        # 塞进 reasoning_content，content 为 null）
        no_rf_raw = cfg.get("no_response_format") or []
        if isinstance(no_rf_raw, str):
            no_rf_raw = [no_rf_raw]
        cfg["no_response_format"] = [
            str(m).strip() for m in no_rf_raw if str(m).strip()
        ]

        # 视觉白名单（vision=True 时限定候选模型；非视觉调用路径完全不读它，
        # 所以不传 vision 的行为与加这个键之前逐字一致）
        vision_raw = cfg.get("vision_models")
        if isinstance(vision_raw, str):
            vision_raw = [vision_raw]
        cfg["vision_models"] = [
            str(m).strip() for m in (vision_raw or []) if str(m).strip()
        ]

        _CONFIG_CACHE = cfg
        return cfg


def load_config() -> dict:
    """公开入口：返回已解析（含 base_url/api_key）的配置副本。"""
    return dict(_load_config())


def available_models() -> list[str]:
    """按优先级链去重后的有序模型列表（供 decision_engine 派生可选模型）。"""
    cfg = _load_config()
    seen: set[str] = set()
    out: list[str] = []
    for tier in cfg["tiers"]:
        for model in tier:
            if model not in seen:
                seen.add(model)
                out.append(model)
    return out


def get_active_model() -> str:
    """读 config/active_model.json 的 model 字段（读不到返回空串）。"""
    try:
        data = json.loads(ACTIVE_MODEL_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("model") or "").strip()


def model_min_max_tokens(model: str) -> int:
    """该模型的 max_tokens 下限（全局安全网 min_max_tokens 与 per-model 覆盖取大）。

    `model_overrides.<model>.min_max_tokens` **只用于把某个模型的下限抬到全局
    之上**：全局值是"思考型模型不再返回空内容"的安全网（PROBE §5.3 建议 512），
    不允许被覆盖值降到它之下（降下去就等于把 R1 的假故障重新放回来）。
    """
    cfg = _load_config()
    floor = int(cfg.get("min_max_tokens") or DEFAULT_MIN_MAX_TOKENS)
    spec = (cfg.get("model_overrides") or {}).get(model) or {}
    try:
        sub = int(spec.get("min_max_tokens"))
    except (TypeError, ValueError):
        sub = 0
    return max(floor, sub) if sub > 0 else floor


def effective_max_tokens(model: str, requested: Optional[int] = None) -> int:
    """实际发给网关的 max_tokens = max(调用方值, 该模型下限)。

    调用方没给（None/0/非法值）时用 config 的 max_tokens_default 作为诉求值，
    再被下限抬高——所以 500 的默认值对 T3/T4/T5 实际生效为 512。
    """
    cfg = _load_config()
    try:
        want = int(requested) if requested is not None else 0
    except (TypeError, ValueError):
        want = 0
    if want <= 0:
        try:
            want = int(cfg.get("max_tokens_default") or DEFAULT_MAX_TOKENS)
        except (TypeError, ValueError):
            want = DEFAULT_MAX_TOKENS
    return max(want, model_min_max_tokens(model))


def supports_response_format(model: str) -> bool:
    """该模型是否可以把 response_format 发给网关（PROBE R3 的 per-model 开关）。"""
    cfg = _load_config()
    return model not in set(cfg.get("no_response_format") or [])


# ---------------------------------------------------------------------------
# 冷却表
# ---------------------------------------------------------------------------


def _is_cooling(model: str) -> bool:
    now = time.monotonic()
    with _COOLDOWN_LOCK:
        until = _COOLDOWN.get(model)
        if until is None:
            return False
        if until <= now:
            _COOLDOWN.pop(model, None)
            return False
        return True


def _mark_cooldown(model: str, seconds: float) -> None:
    with _COOLDOWN_LOCK:
        _COOLDOWN[model] = time.monotonic() + float(seconds)


def cooldown_snapshot() -> dict[str, float]:
    """返回当前仍在冷却中的 {model: 剩余秒数}（诊断用）。"""
    now = time.monotonic()
    with _COOLDOWN_LOCK:
        expired = [m for m, until in _COOLDOWN.items() if until <= now]
        for m in expired:
            _COOLDOWN.pop(m, None)
        return {m: round(until - now, 1) for m, until in _COOLDOWN.items()}


def clear_cooldowns() -> None:
    """清空冷却表（演练与手动恢复用）。"""
    with _COOLDOWN_LOCK:
        _COOLDOWN.clear()


def _is_model_missing_error(status_code: int, body_text: str) -> bool:
    """判断是否为"模型不存在/不支持"类错误（→ 长冷却）。

    只在响应体明确提到模型/不支持字样时判定，避免把普通参数错误
    误判成长冷却（那样会把一个健康模型冷藏 10 分钟）。
    兼容保留：新的分流走 _classify_http_error。
    """
    if status_code not in (400, 404, 422, 500, 501):
        return False
    low = (body_text or "").lower()
    return any(marker in low for marker in _MODEL_MISSING_MARKERS)


# ---------------------------------------------------------------------------
# 错误分流 / 审计脱敏（PROBE R4/R5/§4.4）
# ---------------------------------------------------------------------------


def _sanitize(text: Any, limit: int = _ERR_MESSAGE_LIMIT) -> str:
    """折叠空白 + 打码 sk-* 密钥 + 截断（写进审计 err 前的最后一道闸）。"""
    collapsed = " ".join(str(text or "").split())
    collapsed = _SECRET_RE.sub("sk-****", collapsed)
    return collapsed[:limit]


def _parse_error(body_text: str) -> tuple[str, str]:
    """从错误响应体里取 (error.type, error.message)；取不到返回 ("", 原文)。

    只认顶层 error 对象，**绝不透传 attempts[] 之类的上游内部结构**
    （404 body 的 attempts[].key 是上游 key 持有者人名，PROBE R5）。
    """
    raw = str(body_text or "").strip()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return "", raw
    if not isinstance(data, dict):
        return "", raw
    err = data.get("error")
    if isinstance(err, dict):
        etype = str(err.get("type") or "").strip()
        msg = str(err.get("message") or "").strip() or str(err.get("code") or "").strip()
        return etype, (msg or raw)
    if isinstance(err, str):
        return "", err
    return str(data.get("type") or "").strip(), str(data.get("message") or "").strip() or raw


def _slim_err(
    status_code: int,
    err_type: str,
    message: str,
    body_text: str = "",
    *,
    note: str = "",
) -> str:
    """审计用的一行错误：`HTTP <status>/<error.type>: <message≤200字>`。

    不写整段响应体（404 会回显上游 key 内部名且 >1KB，PROBE R5）。
    """
    etype = _sanitize(err_type, 60) or "unknown"
    msg = _sanitize(message, _ERR_MESSAGE_LIMIT) or _sanitize(
        body_text, _ERR_MESSAGE_LIMIT
    )
    out = f"HTTP {status_code}/{etype}: {msg or '无响应体'}"
    if note:
        out += f" [{note}]"
    return out


def _classify_http_error(
    status_code: int, err_type: str = "", message: str = "", body_text: str = ""
) -> str:
    """把非 200 响应分流成四种处置之一：

    - "auth"          鉴权失败（401 / 鉴权语义 403）→ 立即中止整池，不冷却；
    - "request"       请求侧问题（参数/校验类 400）→ 不冷却，换下一个；
    - "model_missing" 模型不存在/不可用 → 长冷却 600s；
    - "transient"     429/5xx/其它 → 短冷却 60s。
    """
    etype = str(err_type or "").strip().lower()
    low = f"{etype} {message or ''} {body_text or ''}".lower()

    if status_code == 401:
        # 401 是全局故障（所有模型都会 401），重试无意义
        return "auth"
    if status_code == 403:
        # 403 "这个 key 不能访问某个模型" = 该模型不可用（按模型），不是全局鉴权故障；
        # 只有明确的鉴权语义才当作 auth，其余按瞬时故障处理。
        if any(m in low for m in _MODEL_SCOPED_MARKERS):
            return "model_missing"
        if etype in _AUTH_ERROR_TYPES or any(m in low for m in _AUTH_MARKERS):
            return "auth"
        return "transient"

    # 参数/校验类 400（litellm.BadRequestError 等）：对任何模型都会失败，绝不冷却
    if etype in _REQUEST_SIDE_TYPES or any(m in low for m in _REQUEST_SIDE_MARKERS):
        return "request"

    if status_code in (400, 404, 422) and any(
        m in low for m in _MODEL_MISSING_MARKERS
    ):
        return "model_missing"
    if status_code == 404:
        return "model_missing"
    if status_code in (400, 422):
        # 未知的 400：按请求侧处理（宁可不冷却，也不能一次坏请求冻死整池）
        return "request"
    return "transient"


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


def _resolve_log_path() -> Optional[Path]:
    """挑一个真正可写的审计日志路径（结果缓存）。

    首选 logs/llm_pool.jsonl；若该目录/文件不可写（受限环境下会整目录
    拒绝写入），退到 qq_native_re/findings/model_pool/llm_pool.jsonl。
    可用环境变量 LLM_POOL_LOG_PATH 强制指定。

    返回 None 表示两处都不可写——此时审计静默跳过，但绝不拖垮模型调用。
    """
    global _LOG_RESOLVED
    with _LOG_LOCK:
        if _LOG_RESOLVED is not None:
            return _LOG_RESOLVED

        candidates: list[Path] = []
        override = str(os.environ.get("LLM_POOL_LOG_PATH", "")).strip()
        if override:
            candidates.append(Path(override))
        else:
            # 允许 config/model_pool.json 覆盖默认日志位置
            try:
                cfg_log = str(_load_config().get("log_path") or "").strip()
            except Exception:
                cfg_log = ""
            if cfg_log:
                candidates.append(Path(cfg_log))
            candidates.append(LOG_PATH)
            candidates.append(FALLBACK_LOG_PATH)

        for cand in candidates:
            try:
                cand.parent.mkdir(parents=True, exist_ok=True)
                with cand.open("a", encoding="utf-8"):
                    pass
            except OSError:
                continue
            _LOG_RESOLVED = cand
            return cand
        return None


def _rotate_log_if_needed(path: Path) -> None:
    """超 5MB 就轮转为 .1（只保留一份历史，避免磁盘无限增长）。"""
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            rotated = path.with_suffix(path.suffix + ".1")
            try:
                if rotated.exists():
                    rotated.unlink()
            except OSError:
                pass
            path.replace(rotated)
    except OSError:
        pass


def _audit(
    *,
    label: str,
    tier: Any,
    model: str,
    ok: bool,
    latency_ms: int,
    err: Optional[str],
    tokens: Optional[int],
    final: bool = False,
) -> None:
    """追加一行审计 JSON（线程安全，带 5MB 轮转）。

    字段：ts,label,tier,model,ok,latency_ms,err,tokens
    另附 final=True 标记"最终成功的一次调用"，便于一眼看出谁在扛。
    """
    record = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "label": label,
        "tier": tier,
        "model": model,
        "ok": bool(ok),
        "latency_ms": int(latency_ms),
        "err": err,
        "tokens": tokens,
    }
    if final:
        record["final"] = True
    line = json.dumps(record, ensure_ascii=False)
    with _LOG_LOCK:
        path = _resolve_log_path()
        if path is None:
            return
        try:
            _rotate_log_if_needed(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            # 审计失败绝不能拖跨模型调用
            pass


def audit_log_path() -> Optional[Path]:
    """返回实际使用的审计日志路径（诊断用）。"""
    return _resolve_log_path()


# ---------------------------------------------------------------------------
# 核心调用
# ---------------------------------------------------------------------------


def model_supports_vision(model: str) -> bool:
    """该模型是否在视觉白名单里（白名单为空 = 不限，视作都支持）。"""
    allowed = _load_config().get("vision_models") or []
    return (not allowed) or (model in allowed)


def _build_attempt_order(
    preferred: Optional[str] = None, vision: bool = False
) -> list[tuple[Any, str]]:
    """生成 (tier, model) 尝试序列，去重且保持优先级顺序。

    - preferred 在池中 → 置于链首（tier 记为 "preferred"），只试一次。
    - 软开关 LLM_POOL_DISABLE=1 → 只返回 active_model 一项（不降档）。
    - 正在冷却的模型直接跳过。
    - vision=True（V1 新增）：候选**只在 config 的 vision_models 白名单内**，
      白名单为空则回退到原全链（保持向后兼容）。视觉调用不能去试
      qwen3.8-flash-next 这类"收图但只回 reasoning_content"的模型：那会白烧
      一轮预算再降档。优先级顺序 / 冷却 / 软开关逻辑与非 vision 完全共用。
    """
    cfg = _load_config()
    order: list[tuple[Any, str]] = []
    seen: set[str] = set()

    hard_disabled = _soft_disabled()

    # 候选池：vision=True 时收窄到白名单；白名单为空 → 回退原全链
    vision_allowed: Optional[set[str]] = None
    if vision:
        names = [str(m).strip() for m in (cfg.get("vision_models") or [])]
        names = [m for m in names if m]
        if names:
            vision_allowed = set(names)

    def _in_vision_scope(model: str) -> bool:
        return vision_allowed is None or model in vision_allowed

    if not preferred:
        preferred = get_active_model()

    if hard_disabled:
        if not preferred:
            raise LLMPoolExhausted(
                "LLM_POOL_DISABLE=1 且 config/active_model.json 无可用模型，无法调用"
            )
        if _is_cooling(preferred):
            remaining = cooldown_snapshot().get(preferred, 0.0)
            raise LLMPoolExhausted(
                f"LLM_POOL_DISABLE=1 且唯一模型 {preferred} 处于冷却中（剩余 {remaining}s）"
            )
        if not _in_vision_scope(preferred):
            raise LLMPoolExhausted(
                f"LLM_POOL_DISABLE=1（单模型模式，不降档）且唯一模型 {preferred} "
                f"不在视觉白名单 {sorted(vision_allowed)} 内，无法识图"
            )
        return [("disabled", preferred)]

    if (
        preferred
        and any(preferred in tier for tier in cfg["tiers"])
        and _in_vision_scope(preferred)
    ):
        seen.add(preferred)
        if not _is_cooling(preferred):
            order.append(("preferred", preferred))

    for idx, tier in enumerate(cfg["tiers"], start=1):
        for model in tier:
            if model in seen:
                continue
            if not _in_vision_scope(model):
                continue
            seen.add(model)
            if _is_cooling(model):
                continue
            order.append((idx, model))

    return order


def _extract_text(data: dict) -> tuple[str, Optional[int]]:
    """从响应体里取出正文与 token 数；取不到正文抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError(f"响应不是 JSON 对象: {type(data).__name__}")
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("响应缺少 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if content is None:
        content = choices[0].get("text")
    if content is None:
        raise ValueError("响应无 content 字段")
    if isinstance(content, list):
        # 少数网关返回分段 content
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    text = str(content).strip()
    if not text:
        # PROBE §5.2：空内容不能只看 finish_reason（qwen3.8-flash-next 在
        # response_format 下是 finish_reason=stop 且 content=None），
        # 把 finish_reason 与思考 token 明细一起写进错误信息，便于定位。
        finish = choices[0].get("finish_reason")
        usage_ = data.get("usage") or {}
        detail = {}
        if isinstance(usage_, dict):
            detail = usage_.get("completion_tokens_details") or {}
        extra = ""
        if isinstance(detail, dict) and detail:
            extra = (
                f", text_tokens={detail.get('text_tokens')}"
                f", reasoning_tokens={detail.get('reasoning_tokens')}"
            )
        raise ValueError(f"响应 content 为空 (finish_reason={finish}{extra})")
    usage = data.get("usage") or {}
    tokens: Optional[int] = None
    if isinstance(usage, dict):
        for key in ("total_tokens", "completion_tokens"):
            value = usage.get(key)
            if isinstance(value, int):
                tokens = value
                break
    return text, tokens


_CALL_STATS = contextvars.ContextVar("llm_call_stats", default=None)


def _bounded_chat(fn):
    """限制整轮墙钟耗时；排队应在此装饰器外完成。"""
    @functools.wraps(fn)
    async def bounded(*args, **kwargs):
        started = time.monotonic()
        cfg = _load_config()
        budget = float(cfg["total_budget_seconds"])
        stats = {"deadline": started + budget, "attempts": 0, "model": None}
        token = _CALL_STATS.set(stats)
        outcome = "success"
        try:
            return await asyncio.wait_for(fn(*args, **kwargs), budget)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except asyncio.TimeoutError as exc:
            outcome = "total_timeout"
            if stats["model"]:
                _mark_cooldown(stats["model"], cfg["cooldown_seconds"])
            raise LLMPoolExhausted(f"模型池整轮预算 {budget:g}s 已耗尽") from exc
        except Exception as exc:
            outcome = type(exc).__name__
            raise
        finally:
            logging.getLogger("qqbot").info(
                "[模型耗时] label=%s total_ms=%d attempts=%d outcome=%s",
                kwargs.get("label", "general"), int((time.monotonic() - started) * 1000),
                stats["attempts"], outcome,
            )
            _CALL_STATS.reset(token)
    return bounded


@_bounded_chat
async def chat(
    messages: list[dict],
    *,
    label: str = "general",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    response_format: Optional[dict] = None,
    preferred: Optional[str] = None,
    timeout: Optional[float] = None,
    vision: bool = False,
) -> tuple[str, str]:
    """池化调用，返回 (text, model_used)。全部失败抛 LLMPoolExhausted。

    异常（后两个都是 LLMPoolExhausted 的子类，下游 except 不会漏捕获）：
    - LLMPoolExhausted     池内模型都失败（含模型不存在/超时/空内容等）；
    - LLMPoolRequestError  请求本身有问题（参数/校验类 400），不是模型故障；
    - LLMPoolAuthError     网关鉴权失败（401），全局故障，已立即中止整池。

    timeout 给了就用它作为单次尝试上限（仍受 total_budget 约束），
    这样 decision_engine 的子进程 60s 外框也安全。
    max_tokens 会被抬到该模型的下限（config 的 min_max_tokens / model_overrides），
    传小了也不会让思考型模型返回空内容。
    vision=True（V1 新增）时尝试序列只在 config 的 vision_models 白名单内挑
    （白名单为空则回退全链）；messages 里的多模态 content 数组是原样透传给
    /chat/completions 的，冷却/审计/预算/故障转移逻辑与非 vision 完全一致。
    不传 vision（默认 False）时行为与本次改动前逐字一致。
    """
    cfg = _load_config()
    if not cfg.get("api_key"):
        raise LLMPoolExhausted(
            f"未配置 API key（检查 .env 的 {cfg.get('api_key_env')} 或 config/model_pool.json 的 api_key）"
        )

    attempt_timeout = float(
        timeout if timeout and timeout > 0 else cfg["attempt_timeout_seconds"]
    )
    total_budget = float(cfg["total_budget_seconds"])
    cooldown = float(cfg["cooldown_seconds"])
    hard_cooldown = float(cfg["hard_cooldown_seconds"])
    base_url = str(cfg["base_url"]).rstrip("/")
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    order = _build_attempt_order(preferred, vision=vision)
    started = time.monotonic()
    # 每次尝试的记录：model / kind / err。既用于汇总消息，也用于
    # "是否所有失败都是请求侧错误"的判定（→ LLMPoolRequestError）。
    attempts: list[dict[str, str]] = []

    def _record(model_name: str, kind: str, err: str) -> None:
        attempts.append({"model": model_name, "kind": kind, "err": err})

    if not order:
        raise LLMPoolExhausted(
            "模型池中没有可用模型（全部处于冷却中或配置为空）；"
            f"冷却快照={cooldown_snapshot()}"
        )

    # 本地网关：trust_env=False 防止系统代理把 127.0.0.1 也劫持了
    async with httpx.AsyncClient(trust_env=False) as client:
        for tier, model in order:
            elapsed = time.monotonic() - started
            stats = _CALL_STATS.get()
            remaining = min(total_budget - elapsed, stats["deadline"] - time.monotonic())
            if remaining <= 0:
                pending = ", ".join(m for _, m in order if ("preferred" != m))
                _record(
                    "<budget>",
                    "budget",
                    f"总预算 {total_budget:.0f}s 已耗尽，序列中剩余模型未尝试（含 {model}）：{pending}",
                )
                break

            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                # 思考型模型安全网：max(调用方值, 该模型下限)（PROBE R1/§5.3）
                "max_tokens": effective_max_tokens(model, max_tokens),
            }
            if temperature is not None:
                try:
                    payload["temperature"] = float(temperature)
                except (TypeError, ValueError):
                    # 非法温度值原样透传给网关，让它的 400 走"请求侧错误"分流，
                    # 而不是在本地抛 ValueError 把调用点打崩（PROBE R4）
                    payload["temperature"] = temperature
            if response_format is not None and supports_response_format(model):
                # qwen3.8-flash-next 会 HTTP 200 但把 JSON 塞进 reasoning_content、
                # content=null（PROBE R3）→ 该模型不带这个参数
                payload["response_format"] = response_format

            # 允许配置在运行中被改小而立刻生效
            cfg_live = _load_config()
            attempt_budget = min(float(cfg_live["attempt_timeout_seconds"]), remaining)
            http_timeout = min(attempt_timeout, attempt_budget)

            call_started = time.monotonic()
            stats["attempts"] += 1
            stats["model"] = model
            try:
                resp = await asyncio.wait_for(
                    client.post(url, json=payload, headers=headers, timeout=http_timeout),
                    timeout=http_timeout,
                )
            except (asyncio.TimeoutError, httpx.TimeoutException, httpx.TransportError, OSError) as exc:
                latency_ms = int((time.monotonic() - call_started) * 1000)
                err = _sanitize(f"{type(exc).__name__}: {exc}")
                _mark_cooldown(model, cooldown)
                _record(model, "transient", err)
                _audit(
                    label=label,
                    tier=tier,
                    model=model,
                    ok=False,
                    latency_ms=latency_ms,
                    err=err,
                    tokens=None,
                )
                continue
            except Exception as exc:  # 兜底：任何意外都不能让整池崩掉
                latency_ms = int((time.monotonic() - call_started) * 1000)
                err = _sanitize(f"{type(exc).__name__}: {exc}")
                _mark_cooldown(model, cooldown)
                _record(model, "transient", err)
                _audit(
                    label=label,
                    tier=tier,
                    model=model,
                    ok=False,
                    latency_ms=latency_ms,
                    err=err,
                    tokens=None,
                )
                continue

            latency_ms = int((time.monotonic() - call_started) * 1000)

            if resp.status_code != 200:
                try:
                    body_text = resp.text
                except Exception:
                    body_text = ""
                err_type, err_msg = _parse_error(body_text)
                kind = _classify_http_error(
                    resp.status_code, err_type, err_msg, body_text
                )

                if kind == "auth":
                    # 全局故障：所有模型都会 401，重试只会烧光预算 → 立即止损
                    err = _slim_err(resp.status_code, err_type, err_msg, body_text)
                    _record(model, kind, err)
                    _audit(
                        label=label,
                        tier=tier,
                        model=model,
                        ok=False,
                        latency_ms=latency_ms,
                        err=err,
                        tokens=None,
                    )
                    raise LLMPoolAuthError(
                        f"网关鉴权失败，检查 .env 的 {cfg.get('api_key_env')}"
                        f"（或 config/model_pool.json 的 api_key）。{err}"
                        " —— 鉴权类错误是全局故障，已立即中止整池尝试：不降档、不设冷却。"
                    )

                if kind == "request":
                    # 请求侧问题（参数/校验类）：对任何模型都会同样失败 →
                    # 不设冷却、继续试下一个（PROBE R4：绝不能一次坏请求冻死整池）
                    err = _slim_err(resp.status_code, err_type, err_msg, body_text)
                    _record(model, kind, err)
                    _audit(
                        label=label,
                        tier=tier,
                        model=model,
                        ok=False,
                        latency_ms=latency_ms,
                        err=err,
                        tokens=None,
                    )
                    continue

                if kind == "model_missing":
                    _mark_cooldown(model, hard_cooldown)
                    err = _slim_err(
                        resp.status_code,
                        err_type,
                        err_msg,
                        body_text,
                        note=f"模型不存在/不支持，长冷却 {hard_cooldown:.0f}s",
                    )
                else:
                    _mark_cooldown(model, cooldown)
                    err = _slim_err(resp.status_code, err_type, err_msg, body_text)
                _record(model, kind, err)
                _audit(
                    label=label,
                    tier=tier,
                    model=model,
                    ok=False,
                    latency_ms=latency_ms,
                    err=err,
                    tokens=None,
                )
                continue

            try:
                data = resp.json()
                text, tokens = _extract_text(data)
            except Exception as exc:
                err = _sanitize(f"解析失败: {type(exc).__name__}: {exc}")
                _mark_cooldown(model, cooldown)
                _record(model, "parse", err)
                _audit(
                    label=label,
                    tier=tier,
                    model=model,
                    ok=False,
                    latency_ms=latency_ms,
                    err=err,
                    tokens=None,
                )
                continue

            _audit(
                label=label,
                tier=tier,
                model=model,
                ok=True,
                latency_ms=latency_ms,
                err=None,
                tokens=tokens,
                final=True,
            )
            return text, model

    summary = (
        "; ".join(f"{a['model']}: {a['err']}" for a in attempts)
        if attempts
        else "无模型可尝试"
    )

    # 所有失败都是请求侧错误 → 请求本身有问题，不是模型故障（且未设任何冷却）
    if attempts and all(a["kind"] == "request" for a in attempts):
        tried = ", ".join(a["model"] for a in attempts)
        raise LLMPoolRequestError(
            f"请求本身有问题，不是模型故障：参数/校验类错误（HTTP 400），"
            f"池内 {len(attempts)} 个模型（{tried}）全部以同样方式失败，"
            f"已按设计不设任何冷却、不做模型降档定责。失败明细：{summary}"
        )

    if _soft_disabled():
        raise LLMPoolExhausted(
            f"池已关闭（LLM_POOL_DISABLE=1，单模型模式，不降档），"
            f"唯一模型调用失败。失败明细：{summary}"
        )
    raise LLMPoolExhausted(
        f"模型池已耗尽（尝试 {len(attempts)} 项，耗时 {time.monotonic() - started:.1f}s）。失败明细：{summary}"
    )


def chat_sync(
    messages: list[dict],
    *,
    label: str = "general",
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    response_format: Optional[dict] = None,
    preferred: Optional[str] = None,
    timeout: Optional[float] = None,
    vision: bool = False,
) -> tuple[str, str]:
    """同步包装，供非异步调用点使用。"""
    coro = chat(
        messages,
        label=label,
        max_tokens=max_tokens,
        temperature=temperature,
        response_format=response_format,
        preferred=preferred,
        timeout=timeout,
        vision=vision,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop_running = False
    else:
        loop_running = True

    if loop_running:
        # 已在事件循环里：丢到独立线程跑，避免 "asyncio.run() cannot be
        # called from a running event loop"。
        result: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result["value"] = asyncio.run(coro)
            except BaseException as exc:  # noqa: BLE001 - 透传给主线程
                result["error"] = exc

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result["value"]

    return asyncio.run(coro)


if __name__ == "__main__":  # pragma: no cover - 手动自检入口
    cfg = load_config()
    print("base_url     :", cfg["base_url"])
    print("api_key      :", "已配置" if cfg["api_key"] else "缺失")
    print("tiers        :", cfg["tiers"])
    print("available    :", available_models())
    print("active_model :", get_active_model())
    print("max_tokens   : 默认诉求", cfg["max_tokens_default"], "| 全局下限", cfg["min_max_tokens"])
    print("model_overrides   :", cfg["model_overrides"])
    print("no_response_format:", cfg["no_response_format"])
    print("生效下限（传 16 时实际发送的 max_tokens）:")
    for _m in available_models():
        print(f"  {_m:28s} -> {effective_max_tokens(_m, 16)}")
    print("cooldowns    :", cooldown_snapshot())
