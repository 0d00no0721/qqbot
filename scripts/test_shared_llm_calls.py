# -*- coding: utf-8 -*-
"""主进程共享模型池离线验证。"""

import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from scripts import llm_pool as pool, model_primary, model_fallback, reply_prompt
from scripts.decision_engine import DecisionEngine


class SharedPoolTests(unittest.IsolatedAsyncioTestCase):
    def test_prompts_match_pre_refactor_golden_hashes(self):
        target = {"nickname": "甲", "uid": "1", "content": "今天如何"}
        cases = [
            (None, None, "580f031f1580282909626bb725f322e609e42ecb7dd4d69bba287c08cdd6131e"),
            (target, None, "33bc8d4e10c8cdb356b44c28c2ad5c8da45cbe885d704cf6b2aca50fe58839c8"),
            (target, ["旧回复", None, 12], "86b24a44f012ea89ab44eebadc3c4e752cc17e06b4013645cb5e6ade61ea7a7c"),
        ]
        for target, recent, expected in cases:
            for module in (reply_prompt, model_primary, model_fallback):
                actual = module._build_prompt("群聊背景", target, recent)
                self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), expected)

    async def asyncSetUp(self):
        self.cfg = dict(pool.load_config(), api_key="offline-test", tiers=[[f"m{i}" for i in range(9)]])
        self.config = patch.object(pool, "_load_config", return_value=self.cfg)
        self.audit = patch.object(pool, "_audit")
        self.enabled = patch.object(pool, "_soft_disabled", return_value=False)
        for patched in (self.config, self.audit, self.enabled):
            patched.start()
            self.addCleanup(patched.stop)
        pool.clear_cooldowns()
        self.addCleanup(pool.clear_cooldowns)
        self.client_type = httpx.AsyncClient
        self.calls = []
        self.engine = DecisionEngine()
        self.engine._format_context = AsyncMock(return_value="群聊背景")
        self.engine._load_character = lambda _: "测试人设"
        self.engine.get_active_model = lambda: {"model": "m0"}

    def client(self, responder):
        async def handler(request):
            body = json.loads(request.content)
            self.calls.append(body)
            return responder(body)
        return patch.object(pool.httpx, "AsyncClient", side_effect=lambda **kwargs:
                            self.client_type(transport=httpx.MockTransport(handler), **kwargs))

    async def test_all_nine_fail_once_and_next_request_observes_shared_cooldown(self):
        missing = lambda _: httpx.Response(404, json={"error": {"message": "model not found"}})
        with self.client(missing), patch.object(self.engine, "_call_model_script", AsyncMock()) as script:
            self.assertEqual(await self.engine._generate_reply_with_failover("g"), "")
            self.assertEqual(len(self.calls), 9)
            self.assertEqual(len({request["model"] for request in self.calls}), 9)
            with self.assertRaises(pool.LLMPoolExhausted):
                await pool.chat([], preferred="m0", label="vision")
            self.assertEqual(len(self.calls), 9)
            script.assert_not_called()

    async def test_auth_failure_stops_immediately(self):
        with self.client(lambda _: httpx.Response(401, json={"error": {"message": "invalid api key"}})):
            with self.assertRaises(pool.LLMPoolAuthError):
                await pool.chat([], preferred="m0")
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(pool.cooldown_snapshot())

    async def test_content_validation_continues_without_global_cooldown(self):
        def respond(body):
            content = "好" if body["model"] == "m0" else "[测试昵称]: 这是有效回复"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        target = {"uid": "1", "nickname": "甲", "content": "你好"}
        with self.client(respond):
            reply = await self.engine._generate_reply_with_failover("g", target)
        self.assertEqual(reply, "这是有效回复")
        self.assertEqual([r["model"] for r in self.calls], ["m0", "m1"])
        self.assertFalse(pool.cooldown_snapshot())
        self.assertEqual(self.calls[0]["temperature"], 0.85)
        self.assertIn("【你需要回应的最新消息】甲: 你好", self.calls[0]["messages"][1]["content"])

    async def test_rechecks_cooldown_before_each_attempt(self):
        def respond(body):
            if body["model"] == "m0":
                pool._mark_cooldown("m1", 60)  # 模拟另一请求在等待期间发现故障
                return httpx.Response(500, json={"error": {"message": "offline failure"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": "有效回复"}}]})
        with self.client(respond):
            text, model = await pool.chat([], preferred="m0")
        self.assertEqual(model, "m2")
        self.assertEqual([r["model"] for r in self.calls], ["m0", "m2"])

    async def test_cancel_is_not_converted_to_a_retry(self):
        with patch("scripts.decision_engine.chat", AsyncMock(side_effect=asyncio.CancelledError)) as chat:
            with self.assertRaises(asyncio.CancelledError):
                await self.engine._generate_reply_with_failover("g")
        self.assertEqual(chat.await_count, 1)

    async def test_debug_script_generation_keeps_parameters(self):
        for module in (model_primary, model_fallback):
            with self.subTest(module=module.__name__), patch.object(module, "chat", AsyncMock(return_value=("[昵称]: 回复文本", "m0"))) as chat:
                self.assertEqual(await module.generate_reply("背景", "人设", 500, "m0"), "回复文本")
                self.assertEqual(chat.call_args.kwargs["preferred"], "m0")
                self.assertEqual(chat.call_args.kwargs["temperature"], 0.85)

    async def test_legacy_script_entrypoints_output_json_without_network(self):
        for name in ("model_primary.py", "model_fallback.py"):
            proc = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).parent / name),
                       stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await asyncio.wait_for(proc.communicate(b""), 5)
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(json.loads(out), {"reply": ""})


if __name__ == "__main__":
    unittest.main()
