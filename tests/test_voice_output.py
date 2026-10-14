# -*- coding: utf-8 -*-
"""仅使用临时配置、事件屏障和本地 HTTP 桩，不访问 QQ、Edge 或真实 TTS。"""

import asyncio
import base64
import io
import json
import logging
import os
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx

from scripts.voice_output.config import VoiceConfig, validate
from scripts.voice_output.providers import Audio, AudioBuffer, VoiceError, edge, http_audio, validate_audio
from scripts.voice_output.service import VoiceService
from scripts.voice_output.transport import send_record


def wav_bytes(frames=100):
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * frames)
    return out.getvalue()


class Socket:
    closed = False


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def config_data(mode="text_and_voice"):
    return {
        "defaults": {"enabled": True, "mode": mode, "voice": "test",
                     "group_cooldown_seconds": 0, "user_cooldown_seconds": 0},
        "groups": {"100": {}, "200": {}, "300": {}},
        "voices": {"test": {"provider": "test", "voice": "old"}},
        "limits": {"concurrency": 1, "max_pending": 4, "group_pending": 4},
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "voice.json"

    def test_defaults_overrides_and_missing_group_fail_closed(self):
        raw = config_data()
        raw["groups"]["100"] = {"mode": "voice_only", "max_chars": 80, "extra": "kept"}
        cfg = validate(raw)
        group = VoiceConfig.for_group(cfg, "100")
        self.assertEqual(group["mode"], "voice_only")
        self.assertEqual(group["max_chars"], 80)
        self.assertEqual(group["voice"], "test")
        self.assertEqual(group["extra"], "kept")
        self.assertFalse(VoiceConfig.for_group(cfg, "999")["enabled"])
        self.assertEqual(raw["groups"]["100"]["extra"], "kept")

    def test_mtime_cache_corruption_and_recovery(self):
        self.path.write_text(json.dumps(config_data()), encoding="utf-8")
        logger = Mock()
        cache = VoiceConfig(self.path, logger)
        first = cache.load()
        with patch.object(Path, "read_text", side_effect=AssertionError("不应重复读取")):
            self.assertIs(cache.load(), first)
        self.path.write_text("{ broken", encoding="utf-8")
        self.assertIsNone(cache.load())
        self.assertIsNone(cache.load())
        logger.warning.assert_called_once()
        self.path.write_text(json.dumps(config_data("voice_only")), encoding="utf-8")
        self.assertEqual(cache.load()["defaults"]["mode"], "voice_only")
        self.path.unlink()
        self.assertIsNone(cache.load())

    def test_invalid_config_is_rejected(self):
        for section, key, value in (("limits", "concurrency", 0), ("limits", "max_pending", -1),
                                    ("defaults", "enabled", "false"), ("defaults", "mode", "all"),
                                    ("limits", "queue_timeout_seconds", float("nan"))):
            with self.subTest(key=key):
                raw = config_data()
                raw[section][key] = value
                with self.assertRaises(ValueError):
                    validate(raw)

    def test_http_config_and_sovits_reference_validation(self):
        raw = config_data()
        raw["voices"]["test"] = {"provider": "gpt_sovits", "endpoint": "http://127.0.0.1:9880/tts"}
        with self.assertRaises(ValueError):
            validate(raw)
        raw["voices"]["test"]["params"] = {
            "ref_audio_path": "D:/声音/示例.wav", "text_lang": "zh", "prompt_lang": "zh"}
        validate(raw)
        raw["voices"]["test"]["endpoint"] = "http://user:secret@localhost/tts"
        with self.assertRaises(ValueError):
            validate(raw)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "voice.json"
        self.raw = config_data()
        self.write_config()
        self.clock = Clock()
        self.owner = Socket()
        self.client = Mock(aclose=AsyncMock())
        self.provider = AsyncMock(return_value=Audio(wav_bytes(), "wav"))
        self.sender = AsyncMock(return_value=123)
        self.logger = Mock()
        self.service = VoiceService(self.path, clock=self.clock, client=self.client,
                                    providers={"test": self.provider}, sender=self.sender, logger=self.logger)
        self.text = AsyncMock(return_value=True)

    async def asyncTearDown(self):
        await self.service.close()
        self.temp.cleanup()

    def write_config(self):
        self.path.write_text(json.dumps(self.raw), encoding="utf-8")

    def submit(self, mid, gid="100", uid="1", **kwargs):
        return self.service.submit(owner=kwargs.pop("owner", self.owner), group_id=gid, user_id=uid,
                                   message_id=mid, text=kwargs.pop("text", "离线测试"), kind="reply",
                                   on_failure=kwargs.pop("failure", None), **kwargs)

    async def reply(self, mid=1, gid="100", text="你好呀", is_at=True):
        return await self.service.reply(self.owner, gid, "1", mid, text, self.text, is_at=is_at)

    async def test_idle_and_disabled_do_not_start_tasks_or_network(self):
        await self.reply(gid="999")
        self.provider.assert_not_awaited()
        self.sender.assert_not_awaited()
        self.assertFalse(self.service._active)
        self.assertFalse(self.service._pending)
        self.text.assert_awaited_once_with("你好呀")

    async def test_text_first_and_slow_synthesis_does_not_block(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            self.text.assert_awaited_once_with("你好呀")
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        await asyncio.wait_for(self.reply(), 1)
        await entered.wait()
        await self.text("另一条普通消息")
        self.sender.assert_not_awaited()
        release.set()
        await self.service.drain()
        self.sender.assert_awaited_once()

    async def test_voice_only_success_sends_no_text(self):
        self.raw["defaults"]["mode"] = "voice_only"
        self.write_config()
        await self.reply()
        await self.service.drain()
        self.text.assert_not_awaited()
        self.sender.assert_awaited_once()

    async def test_voice_only_full_queue_falls_back_immediately(self):
        self.raw["defaults"]["mode"] = "voice_only"
        self.raw["limits"]["max_pending"] = 0
        self.write_config()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        await self.reply(1)
        await entered.wait()
        await asyncio.wait_for(self.reply(2, text="下一条"), 1)
        self.text.assert_awaited_once_with("下一条")
        release.set()
        await self.service.drain()
        self.sender.assert_awaited_once()

    async def test_voice_only_failure_falls_back_once_without_second_tts(self):
        self.raw["defaults"]["mode"] = "voice_only"
        self.write_config()
        self.provider.side_effect = VoiceError("offline_failure")
        await self.reply()
        await self.service.drain()
        self.text.assert_awaited_once_with("你好呀")
        self.provider.assert_awaited_once()
        self.sender.assert_not_awaited()

    async def test_text_and_voice_failure_does_not_repeat_text(self):
        self.provider.side_effect = VoiceError("offline_failure")
        await self.reply()
        await self.service.drain()
        self.text.assert_awaited_once_with("你好呀")

    async def test_long_text_only_at_and_bad_config_keep_text(self):
        await self.reply(text="长" * 201)
        await self.reply(2, is_at=False)
        self.path.write_text("{", encoding="utf-8")
        await self.reply(3)
        self.assertEqual(self.text.await_count, 3)
        self.provider.assert_not_awaited()

    async def test_failed_text_send_does_not_start_voice(self):
        self.text.return_value = False
        self.assertFalse(await self.reply())
        self.provider.assert_not_awaited()

    async def test_flood_has_one_active_four_waiting(self):
        release, entered = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        results = [self.submit(n) for n in range(20)]
        await entered.wait()
        self.assertEqual(results.count("accepted"), 5)
        self.assertEqual(results.count("queue_full"), 15)
        self.assertEqual(len(self.service._active), 1)
        self.assertEqual(len(self.service._pending), 4)
        self.assertEqual(self.provider.await_count, 1)
        release.set()
        await self.service.drain()
        self.assertEqual(self.provider.await_count, 5)
        self.assertFalse(self.service._active)

    async def test_same_group_serial_other_group_can_start(self):
        self.raw["limits"]["concurrency"] = 2
        self.write_config()
        release = asyncio.Event()
        entered = asyncio.Queue()
        async def slow(text, *args):
            await entered.put(text)
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        self.submit(1, text="甲1")
        self.submit(2, text="甲2")
        self.submit(3, gid="200", text="乙1")
        self.assertEqual({await entered.get(), await entered.get()}, {"甲1", "乙1"})
        self.assertEqual(len(self.service._pending), 1)
        release.set()
        await self.service.drain()
        self.assertEqual(await entered.get(), "甲2")

    async def test_expired_waiting_request_falls_back_without_synthesis(self):
        release, entered = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        fallback = AsyncMock()
        self.submit(1)
        self.submit(2, failure=fallback)
        await entered.wait()
        self.clock.now += 31
        release.set()
        await self.service.drain()
        fallback.assert_awaited_once_with("expired")
        self.provider.assert_awaited_once()
        self.sender.assert_awaited_once()

    async def test_started_work_can_finish_after_queue_deadline(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        self.submit(1)
        await entered.wait()
        self.clock.now += 100
        release.set()
        await self.service.drain()
        self.sender.assert_awaited_once()

    async def test_dedup_and_cooldowns(self):
        self.raw["defaults"].update(group_cooldown_seconds=5, user_cooldown_seconds=10)
        self.write_config()
        self.assertEqual(self.submit(1), "accepted")
        await self.service.drain()
        self.assertEqual(self.submit(1), "duplicate")
        self.assertEqual(self.submit(2, uid="2"), "cooldown")
        self.clock.now += 5
        self.assertEqual(self.submit(2), "cooldown")
        self.assertEqual(self.submit(2, uid="2"), "accepted")
        await self.service.drain()
        self.clock.now += 5
        self.assertEqual(self.submit(3), "accepted")
        await self.service.drain()

    async def test_state_capacity_evicts_one_entry_not_whole_cache(self):
        self.raw["limits"]["state_entries"] = 16
        self.write_config()
        for i in range(25):
            self.assertEqual(self.submit(i, uid=str(i)), "accepted")
            await self.service.drain()
        self.assertEqual(len(self.service._seen), 16)
        self.assertLessEqual(len(self.service._rates), 16)
        self.assertEqual(self.submit(24), "duplicate")

    async def test_pending_keeps_voice_and_deadline_after_config_change(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        self.submit(1)
        self.submit(2)
        await entered.wait()
        old_deadline = self.service._pending[0].deadline
        self.raw["voices"]["test"]["voice"] = "new"
        self.raw["limits"].update(max_pending=0, queue_timeout_seconds=1)
        self.write_config()
        self.assertEqual(self.submit(3), "queue_full")
        self.assertEqual(self.service._pending[0].deadline, old_deadline)
        self.clock.now += 2
        release.set()
        await self.service.drain()
        self.assertEqual([call.args[1]["voice"] for call in self.provider.await_args_list], ["old", "old"])

    async def test_buffer_reservations_include_work_in_progress(self):
        self.raw["limits"].update(concurrency=2, max_audio_bytes=1024, max_buffer_bytes=1024)
        self.write_config()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
            return Audio(wav_bytes(), "wav")
        self.provider.side_effect = slow
        self.submit(1)
        self.submit(2, gid="200")
        await entered.wait()
        self.assertEqual(self.service._reserved, 1024)
        self.assertEqual(self.provider.await_count, 1)
        release.set()
        await self.service.drain()
        self.assertEqual(self.service._reserved, 0)

    async def test_disconnect_cancels_and_prevents_late_submission(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.provider.side_effect = slow
        fallback = AsyncMock()
        self.submit(1, failure=fallback)
        self.submit(2, failure=fallback)
        await entered.wait()
        await self.service.disconnect(self.owner)
        await cancelled.wait()
        self.assertFalse(self.service._pending)
        self.assertEqual(self.service._reserved, 0)
        self.assertEqual(self.submit(3), "disconnected")
        fallback.assert_not_awaited()
        self.sender.assert_not_awaited()

    async def test_timeout_reclaims_slot_and_pauses_remote_provider(self):
        self.raw["limits"]["synthesis_timeout_seconds"] = 1
        self.raw["voices"]["test"]["endpoint"] = "http://127.0.0.1:9880/tts"
        self.write_config()
        cancelled = asyncio.Event()
        async def slow(*args):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.provider.side_effect = slow
        fallback = AsyncMock()
        self.submit(1, failure=fallback)
        self.submit(2, failure=fallback)
        await asyncio.wait_for(self.service.drain(), 3)
        self.assertTrue(cancelled.is_set())
        self.provider.assert_awaited_once()
        self.assertEqual(self.submit(3), "provider_paused")
        self.assertEqual(self.service._reserved, 0)
        self.assertEqual([c.args[0] for c in fallback.await_args_list], ["synthesis_timeout", "provider_paused"])
        self.raw["defaults"]["max_chars"] = 199
        self.write_config()
        self.provider.side_effect = None
        self.assertEqual(self.submit(4), "accepted")
        await self.service.drain()

    async def test_send_timeout_no_retry_voice_only_fallback(self):
        self.raw["defaults"]["mode"] = "voice_only"
        self.raw["limits"]["send_timeout_seconds"] = 1
        self.write_config()
        async def slow_send(*args):
            await asyncio.Event().wait()
        self.sender.side_effect = slow_send
        await self.reply()
        await asyncio.wait_for(self.service.drain(), 3)
        self.sender.assert_awaited_once()
        self.text.assert_awaited_once_with("你好呀")

    async def test_remote_transport_error_pauses_endpoint_and_logs_no_content(self):
        self.raw["voices"]["test"]["endpoint"] = "http://tts.test/tts"
        self.write_config()
        self.provider.side_effect = httpx.ReadError("secret-and-message-body")
        self.submit(1, text="私有正文")
        await self.service.drain()
        self.assertEqual(self.submit(2), "provider_paused")
        logs = str(self.logger.mock_calls)
        self.assertNotIn("secret-and-message-body", logs)
        self.assertNotIn("私有正文", logs)
        self.assertNotIn("http://tts.test", logs)

    async def test_disconnect_during_send_holds_slot_until_cleanup_finishes(self):
        self.raw["limits"]["concurrency"] = 1
        self.write_config()
        entered, cleanup_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def slow_send(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleanup_entered.set()
                await release.wait()
        self.sender.side_effect = slow_send
        fallback = AsyncMock()
        self.submit(1, failure=fallback)
        await entered.wait()
        disconnect = asyncio.create_task(self.service.disconnect(self.owner))
        await cleanup_entered.wait()
        other = Socket()
        self.assertEqual(self.submit(2, gid="200", owner=other), "accepted")
        self.assertEqual(self.provider.await_count, 1)
        self.assertGreater(self.service._reserved, 0)
        self.sender.side_effect = None
        release.set()
        await disconnect
        await self.service.drain()
        self.assertEqual(self.provider.await_count, 2)
        fallback.assert_not_awaited()

    async def test_close_cleans_client_and_no_late_reply(self):
        entered = asyncio.Event()
        async def slow(*args):
            entered.set()
            await asyncio.Event().wait()
        self.provider.side_effect = slow
        self.submit(1)
        await entered.wait()
        await self.service.close()
        self.client.aclose.assert_awaited_once()
        self.sender.assert_not_awaited()
        self.assertEqual(self.submit(2), "disconnected")

    async def test_read_command_enabled_when_automatic_mode_off_and_notices_limited(self):
        self.raw["defaults"]["mode"] = "off"
        self.write_config()
        await self.service.read(self.owner, "100", "1", 1, "你好", self.text)
        await self.service.drain()
        self.sender.assert_awaited_once()
        await self.service.read(self.owner, "100", "1", 2, "太长" * 200, self.text)
        await self.service.read(self.owner, "100", "1", 3, "太长" * 200, self.text)
        self.assertEqual(self.text.await_count, 1)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_edge_streaming_and_resource_close(self):
        closed = asyncio.Event()
        mp3 = b"\xff\xfb\x90\x64" + bytes(200)
        async def stream():
            try:
                yield {"type": "WordBoundary", "text": "不会落日志"}
                yield {"type": "audio", "data": mp3}
            finally:
                closed.set()
        module = types.SimpleNamespace(Communicate=Mock(return_value=types.SimpleNamespace(stream=stream)))
        with patch.dict(sys.modules, {"edge_tts": module}):
            audio = await edge("你好", {"voice": "test-voice"}, None, 1024)
        self.assertEqual(audio.data, mp3)
        self.assertTrue(closed.is_set())
        self.assertEqual(module.Communicate.call_args.kwargs["voice"], "test-voice")

    async def test_edge_cancel_closes_stream(self):
        entered, closed = asyncio.Event(), asyncio.Event()
        async def stream():
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closed.set()
        module = types.SimpleNamespace(Communicate=Mock(return_value=types.SimpleNamespace(stream=stream)))
        with patch.dict(sys.modules, {"edge_tts": module}):
            task = asyncio.create_task(edge("你好", {}, None, 1024))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(closed.is_set())

    async def test_edge_missing_is_optional(self):
        with patch.dict(sys.modules, {"edge_tts": None}):
            with self.assertRaisesRegex(VoiceError, "edge_not_installed"):
                await edge("你好", {}, None, 1024)

    async def test_sovits_v4_request_contract(self):
        requests = []
        async def handler(request):
            requests.append(request)
            return httpx.Response(200, content=wav_bytes(), headers={"Content-Type": "audio/wav"})
        voice = {"provider": "gpt_sovits", "endpoint": "http://tts.test/tts", "token_env": "TEST_TTS_TOKEN",
                 "params": {"text_lang": "zh", "ref_audio_path": "D:/参考/示例.wav",
                            "prompt_text": "离线测试文本", "prompt_lang": "zh", "sample_steps": 32,
                            "text": "不能覆盖正文", "streaming_mode": True}}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TEST_TTS_TOKEN": "offline-placeholder"}):
                result = await http_audio("本轮文字", voice, client, 1024)
        body = json.loads(requests[0].content)
        self.assertEqual(body["text"], "本轮文字")
        self.assertFalse(body["streaming_mode"])
        self.assertEqual(body["media_type"], "wav")
        self.assertEqual(body["batch_size"], 1)
        self.assertEqual(body["ref_audio_path"], voice["params"]["ref_audio_path"])
        self.assertEqual(requests[0].url.path, "/tts")
        self.assertEqual(len(requests), 1)  # 没有调用任何切换权重/控制接口。
        self.assertEqual(result.format, "wav")

    async def test_generic_http_provider(self):
        async def handler(request):
            self.assertEqual(json.loads(request.content), {"input": "你好", "speaker": "custom"})
            return httpx.Response(200, content=wav_bytes(), headers={"Content-Type": "audio/wav"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await http_audio("你好", {"provider": "http", "endpoint": "http://tts.test/speak",
                                     "text_field": "input", "params": {"speaker": "custom"}}, client, 1024)

    async def test_http_errors_oversized_and_corrupt_audio(self):
        cases = [httpx.Response(500, json={"private": "do not log"}),
                 httpx.Response(200, json={"error": "not audio"}),
                 httpx.Response(200, content=bytes(2048), headers={"Content-Type": "audio/wav"}),
                 httpx.Response(200, content=b"bad wave", headers={"Content-Type": "audio/wav"})]
        for response in cases:
            with self.subTest(response=response):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as client:
                    with self.assertRaises(VoiceError):
                        await http_audio("你好", {"provider": "http", "endpoint": "http://tts.test/tts"}, client, 1024)

    async def test_unknown_length_stream_is_bounded_and_closed(self):
        closed = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(20):
                    yield bytes(1024)
            async def aclose(self):
                closed.set()
        def handler(request):
            return httpx.Response(200, stream=Stream(), headers={"Content-Type": "audio/wav"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(VoiceError, "audio_too_large"):
                await http_audio("你好", {"provider": "http", "endpoint": "http://tts.test/tts"}, client, 1024)
        self.assertTrue(closed.is_set())

    async def test_buffer_rejects_stream_over_limit_and_audio_validation(self):
        buffer = AudioBuffer(1024)
        buffer.append(bytes(1000))
        with self.assertRaises(VoiceError):
            buffer.append(bytes(25))
        for data, format in ((b"ID3" + bytes(20), "mp3"), (wav_bytes()[:-2], "wav"), (b"", "wav")):
            with self.assertRaises(VoiceError):
                validate_audio(data, format)

    async def test_real_local_http_slow_stream_keeps_event_loop_available(self):
        entered, release = asyncio.Event(), asyncio.Event()
        tasks = set()
        audio = wav_bytes()
        async def handle(reader, writer):
            task = asyncio.current_task()
            tasks.add(task)
            try:
                header = await reader.readuntil(b"\r\n\r\n")
                size = next(int(line.split(b":", 1)[1]) for line in header.split(b"\r\n")
                            if line.lower().startswith(b"content-length:"))
                await reader.readexactly(size)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: audio/wav\r\nContent-Length: "
                             + str(len(audio)).encode() + b"\r\nConnection: close\r\n\r\n" + audio[:20])
                await writer.drain()
                entered.set()
                await release.wait()
                writer.write(audio[20:])
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                tasks.discard(task)
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                task = asyncio.create_task(http_audio("你好", {"provider": "http",
                    "endpoint": f"http://127.0.0.1:{port}/tts"}, client, 1024))
                await asyncio.wait_for(entered.wait(), 2)
                tick = asyncio.Event()
                asyncio.get_running_loop().call_soon(tick.set)
                await asyncio.wait_for(tick.wait(), 1)
                self.assertFalse(task.done())
                release.set()
                self.assertEqual((await task).data, audio)
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_chunked_record_and_cooperative_encoding(self):
        received = asyncio.Future()
        audio = Audio(wav_bytes(100000), "wav")
        async def handle(reader, writer):
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                self.assertIn(b"transfer-encoding: chunked", headers.lower())
                body = bytearray()
                while True:
                    count = int((await reader.readline()).strip(), 16)
                    if not count:
                        await reader.readexactly(2)
                        break
                    body.extend(await reader.readexactly(count))
                    await reader.readexactly(2)
                data = json.loads(body)
                value = data["message"][0]["data"]["file"]
                self.assertEqual(base64.b64decode(value[9:]), audio.data)
                received.set_result(data)
                response = b'{"status":"ok","retcode":0,"data":{"message_id":123}}'
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                             + str(len(response)).encode() + b"\r\nConnection: close\r\n\r\n" + response)
                await writer.drain()
            except Exception as exc:
                if not received.done():
                    received.set_exception(exc)
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                result = await asyncio.wait_for(send_record(client, {"base_url": f"http://127.0.0.1:{port}"}, "100", audio), 3)
                self.assertEqual(result, 123)
                self.assertEqual((await received)["group_id"], 100)
        finally:
            server.close()
            await server.wait_closed()

    async def test_record_segment_and_success_receipt(self):
        audio = Audio(wav_bytes(), "wav")
        async def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(request.url.path, "/send_group_msg")
            self.assertEqual(payload["group_id"], 100)
            self.assertEqual(len(payload["message"]), 1)
            segment = payload["message"][0]
            self.assertEqual(segment["type"], "record")
            self.assertEqual(base64.b64decode(segment["data"]["file"].removeprefix("base64://")), audio.data)
            self.assertEqual(request.headers["authorization"], "Bearer placeholder")
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": -123}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(os.environ, {"TEST_NAPCAT_TOKEN": "placeholder"}):
                result = await send_record(client, {"base_url": "http://napcat.test", "token_env": "TEST_NAPCAT_TOKEN"}, "100", audio)
        self.assertEqual(result, -123)

    async def test_failed_malformed_or_placeholder_receipt_never_retries(self):
        cases = [{"status": "failed", "retcode": 1400}, {"status": "ok", "retcode": 0, "data": {"message_id": 0}},
                 {"status": "async", "retcode": 1}, [], {"status": "ok", "retcode": 0}]
        for response in cases:
            with self.subTest(response=response):
                calls = []
                def handler(request):
                    calls.append(request)
                    return httpx.Response(200, json=response)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(VoiceError):
                        await send_record(client, {"base_url": "http://napcat.test"}, "100", Audio(wav_bytes(), "wav"))
                self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
