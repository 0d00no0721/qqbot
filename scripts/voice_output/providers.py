# -*- coding: utf-8 -*-
"""TTS 适配器。只产出有大小限制的音频，不接触 QQ、模型池或聊天状态。"""

import io
import os
import wave
from dataclasses import dataclass


class VoiceError(Exception):
    """不包含正文、密钥和服务响应体的可记录错误。"""


@dataclass(frozen=True)
class Audio:
    data: bytes
    format: str


def auth_headers(token_env):
    token = os.getenv(token_env, "") if token_env else ""
    return {"Authorization": "Bearer " + token} if token else {}


def validate_audio(data, format):
    if format == "wav":
        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                frames = audio.getnframes()
                expected = frames * audio.getnchannels() * audio.getsampwidth()
                if frames <= 0 or expected > len(data) or len(audio.readframes(frames)) != expected:
                    raise VoiceError("invalid_audio")
        except (wave.Error, EOFError):
            raise VoiceError("invalid_audio") from None
    elif format == "mp3":
        offset = 0
        if data.startswith(b"ID3") and len(data) >= 10:
            if any(v & 128 for v in data[6:10]):
                raise VoiceError("invalid_audio")
            offset = 10 + sum(v << shift for v, shift in zip(data[6:10], (21, 14, 7, 0)))
        # Edge 返回 MP3 帧。拒绝空数据、只有标签的数据和伪装成音频的 HTML/JSON。
        if len(data) < offset + 4 or data[offset] != 255 or data[offset + 1] & 224 != 224:
            raise VoiceError("invalid_audio")
    else:
        raise VoiceError("unsupported_audio")
    return Audio(bytes(data), format)


class AudioBuffer:
    def __init__(self, limit):
        self.limit = limit
        self.data = bytearray()

    def append(self, chunk):
        if len(self.data) + len(chunk) > self.limit:
            raise VoiceError("audio_too_large")
        self.data.extend(chunk)

    def finish(self, format):
        return validate_audio(self.data, format)


async def edge(text, voice, client, limit):
    try:
        import edge_tts  # 可选依赖，仅真正使用 Edge 时加载。
    except ImportError:
        raise VoiceError("edge_not_installed") from None
    stream = edge_tts.Communicate(
        text, voice=voice.get("voice", "zh-CN-XiaoxiaoNeural"),
        rate=voice.get("rate", "+0%"), pitch=voice.get("pitch", "+0Hz"),
        volume=voice.get("volume", "+0%"),
    ).stream()
    buffer = AudioBuffer(limit)
    try:
        async for chunk in stream:
            if chunk["type"] == "audio":
                buffer.append(chunk["data"])
    finally:
        await stream.aclose()
    return buffer.finish("mp3")


async def http_audio(text, voice, client, limit):
    params = dict(voice.get("params", {}))
    if voice["provider"] == "gpt_sovits":
        # api_v2.py 的接口同时服务 V4。权重由服务启动配置选择，不逐消息切换。
        params.update(text=text, media_type="wav", streaming_mode=False)
        params.setdefault("batch_size", 1)
        format = "wav"
    else:
        params[voice.get("text_field", "text")] = text
        format = voice.get("format", "wav")
    buffer = AudioBuffer(limit)
    async with client.stream("POST", voice["endpoint"], json=params,
                             headers=auth_headers(voice.get("token_env"))) as response:
        if response.status_code != 200:
            raise VoiceError("tts_http_" + str(response.status_code))
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        if content_type not in ("audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg",
                                "audio/mp3", "application/octet-stream"):
            raise VoiceError("tts_not_audio")
        length = response.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > limit):
            raise VoiceError("audio_too_large")
        async for chunk in response.aiter_bytes(chunk_size=16384):
            buffer.append(chunk)
    return buffer.finish(format)


PROVIDERS = {"edge": edge, "gpt_sovits": http_audio, "http": http_audio}
