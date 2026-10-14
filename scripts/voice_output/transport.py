# -*- coding: utf-8 -*-
"""通过 NapCat HTTP 发送独立 record 段，检查业务回执，不自动重发。"""

import base64
import asyncio
import json

from .providers import VoiceError, auth_headers


async def send_record(client, napcat, group_id, audio):
    # 使用内联数据，兼容 NapCat 与机器人不同目录/不同主机；无临时文件生命周期竞态。
    async def payload():
        yield ('{"group_id":%d,"message":[{"type":"record","data":{"file":"base64://' % int(group_id)).encode()
        # 以 3 的倍数切块，连接后的 Base64 仍是同一个完整文件。
        # 避免一次编码/序列化数 MiB 阻塞消息循环，也不额外保留整份 Base64/JSON。
        view = memoryview(audio.data)
        for offset in range(0, len(view), 3 * 16384):
            yield base64.b64encode(view[offset:offset + 3 * 16384])
            await asyncio.sleep(0)
        yield b'"}}]}'
    headers = {"Content-Type": "application/json", **auth_headers(napcat.get("token_env"))}
    async with client.stream("POST", napcat["base_url"].rstrip("/") + "/send_group_msg",
                             content=payload(), headers=headers) as response:
        if response.status_code != 200:
            raise VoiceError("napcat_http_" + str(response.status_code))
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=4096):
            body.extend(chunk)
            if len(body) > 65536:
                raise VoiceError("napcat_invalid_receipt")
    try:
        result = json.loads(body)
        if result.get("status") != "ok" or result.get("retcode") != 0:
            raise VoiceError("napcat_rejected")
        message_id = result["data"]["message_id"]
        if isinstance(message_id, bool) or not isinstance(message_id, (str, int)) or str(message_id) in ("", "0"):
            raise VoiceError("napcat_invalid_receipt")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise VoiceError("napcat_invalid_receipt") from None
    return message_id
