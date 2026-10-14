# -*- coding: utf-8 -*-
"""独立语音配置；只在使用时检查 mtime，坏配置关闭语音，不影响文字。"""

import copy
import json
import logging
import math
from pathlib import Path
from urllib.parse import urlsplit


DEFAULTS = {
    "enabled": False, "mode": "off", "voice": "edge-xiaoxiao",
    "read_command": True, "only_at": True, "max_chars": 200,
    "group_cooldown_seconds": 5, "user_cooldown_seconds": 10,
    "notice_cooldown_seconds": 10,
}
LIMITS = {
    "concurrency": 1, "max_pending": 4, "group_pending": 2,
    "queue_timeout_seconds": 30, "synthesis_timeout_seconds": 30,
    "send_timeout_seconds": 10, "max_audio_bytes": 8 * 1024 * 1024,
    "max_buffer_bytes": 32 * 1024 * 1024,
    "state_entries": 2048, "dedup_seconds": 600,
}
NAPCAT = {"base_url": "http://127.0.0.1:3000", "token_env": "NAPCAT_HTTP_TOKEN"}
_BOUNDS = {
    "concurrency": (1, 8), "max_pending": (0, 100), "group_pending": (0, 20),
    "queue_timeout_seconds": (1, 300), "synthesis_timeout_seconds": (1, 300),
    "send_timeout_seconds": (1, 60), "max_audio_bytes": (1024, 16 * 1024 * 1024),
    "max_buffer_bytes": (1024, 128 * 1024 * 1024), "state_entries": (16, 10000),
    "dedup_seconds": (1, 3600), "max_chars": (1, 2000),
    "group_cooldown_seconds": (0, 3600), "user_cooldown_seconds": (0, 3600),
    "notice_cooldown_seconds": (0, 3600),
}
_INTEGER_KEYS = {"concurrency", "max_pending", "group_pending", "max_audio_bytes",
                 "max_buffer_bytes", "state_entries", "max_chars"}


def _numbers(data):
    for key, (low, high) in _BOUNDS.items():
        if key not in data:
            continue
        value = data[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high
                or (key in _INTEGER_KEYS and not isinstance(value, int))):
            raise ValueError("语音数值配置无效: " + key)


def _group(data):
    if not isinstance(data, dict):
        raise ValueError("语音群配置须为对象")
    for key in ("enabled", "read_command", "only_at"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError("语音开关须为布尔值: " + key)
    if data.get("mode", "off") not in ("off", "text_and_voice", "voice_only"):
        raise ValueError("语音 mode 无效")
    if "voice" in data and not isinstance(data["voice"], str):
        raise ValueError("语音 voice 无效")
    _numbers(data)


def _url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("语音服务地址须为 HTTP(S)，凭据请使用环境变量")


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("语音配置须为对象")
    result = copy.deepcopy(raw)
    for key in ("defaults", "groups", "voices", "limits", "napcat"):
        if not isinstance(result.setdefault(key, {}), dict):
            raise ValueError("语音配置字段须为对象: " + key)
    _group(result["defaults"])
    for group in result["groups"].values():
        _group(group)
    result["defaults"] = {**DEFAULTS, **result["defaults"]}
    result["limits"] = {**LIMITS, **result["limits"]}
    _numbers(result["limits"])
    if result["limits"]["max_buffer_bytes"] < result["limits"]["max_audio_bytes"]:
        raise ValueError("语音总缓冲上限不能小于单条上限")
    result["napcat"] = {**NAPCAT, **result["napcat"]}
    _url(result["napcat"]["base_url"])
    if not isinstance(result["napcat"]["token_env"], str):
        raise ValueError("NapCat token_env 须为环境变量名")
    for voice in result["voices"].values():
        if not isinstance(voice, dict) or not isinstance(voice.get("provider"), str):
            raise ValueError("音色须指定 provider")
        if voice["provider"] in ("gpt_sovits", "http"):
            _url(voice.get("endpoint", ""))
            if not isinstance(voice.get("params", {}), dict):
                raise ValueError("TTS params 须为对象")
            if not isinstance(voice.get("token_env", ""), str):
                raise ValueError("TTS token_env 须为环境变量名")
        if voice["provider"] == "http":
            if voice.get("format", "wav") not in ("wav", "mp3"):
                raise ValueError("HTTP TTS 仅支持 WAV/MP3")
            if not isinstance(voice.get("text_field", "text"), str):
                raise ValueError("HTTP TTS text_field 无效")
        if voice["provider"] == "gpt_sovits":
            params = voice.get("params", {})
            for key in ("ref_audio_path", "text_lang", "prompt_lang"):
                if not isinstance(params.get(key), str) or not params[key].strip():
                    raise ValueError("GPT-SoVITS 缺少参数: " + key)
    return result


class VoiceConfig:
    def __init__(self, path, logger=None):
        self.path = Path(path)
        self.logger = logger or logging.getLogger("qqbot")
        self._stamp = object()
        self._data = None

    def load(self):
        try:
            stat = self.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp == self._stamp:
            return self._data
        self._stamp = stamp
        self._data = None
        if stamp is None:
            return None
        try:
            if stamp[1] > 256 * 1024:
                raise ValueError("语音配置超过 256 KiB")
            self._data = validate(json.loads(self.path.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError, TypeError, AttributeError, RecursionError):
            # 不回显配置内容、URL 或 token；同一损坏文件只记一次。
            self.logger.warning("[语音] 配置读取失败，已关闭语音，文字照常发送")
        return self._data

    @staticmethod
    def for_group(config, group_id):
        # 独立白名单：即使公共 enabled 为 true，未登记的群仍不开语音。
        if not config or str(group_id) not in config["groups"]:
            return {**DEFAULTS}
        return {**config["defaults"], **config["groups"][str(group_id)]}
