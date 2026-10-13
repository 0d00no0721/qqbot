# -*- coding: utf-8 -*-
"""离线复现：python -m unittest scripts.test_llm_timeouts -v。"""

import asyncio
import json
import sys
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from scripts import llm_pool as pool
from scripts import process_guard


class PoolTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = dict(pool.load_config(), api_key="offline-test", tiers=[["a", "b", "c"]],
                        attempt_timeout_seconds=0.05, total_budget_seconds=0.13)
        self.config = patch.object(pool, "_load_config", return_value=self.cfg)
        self.config.start()
        self.audit = patch.object(pool, "_audit")
        self.audit.start()
        self.env = patch.object(pool, "_soft_disabled", return_value=False)
        self.env.start()
        pool.clear_cooldowns()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.audit.stop)
        self.addCleanup(self.env.stop)
        self.addCleanup(pool.clear_cooldowns)

    async def test_total_budget_covers_all_attempts(self):
        calls = []
        async def blocked(request):
            calls.append(json.loads(request.content)["model"])
            await asyncio.Event().wait()
        client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
        start = time.monotonic()
        with patch.object(pool.httpx, "AsyncClient", return_value=client), self.assertLogs("qqbot") as logs:
            with self.assertRaises(pool.LLMPoolExhausted):
                await pool.chat([], preferred="a")
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls, ["a", "b", "c"][:len(calls)])
        self.assertIn(f"attempts={len(calls)}", "\n".join(logs.output))
        self.assertTrue(client.is_closed)

    async def test_attempt_timeout_can_fall_back(self):
        async def respond(request):
            if json.loads(request.content)["model"] == "a":
                await asyncio.Event().wait()
            return httpx.Response(200, json={"choices": [{"message": {"content": "有效回复"}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch.object(pool.httpx, "AsyncClient", return_value=client):
            text, model = await pool.chat([], preferred="a")
        self.assertEqual((text, model), ("有效回复", "b"))
        self.assertIn("a", pool.cooldown_snapshot())

    async def test_cancel_does_not_fail_over_or_cool_model(self):
        entered = asyncio.Event()
        calls = []
        async def blocked(request):
            calls.append(request)
            entered.set()
            await asyncio.Event().wait()
        client = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
        with patch.object(pool.httpx, "AsyncClient", return_value=client):
            task = asyncio.create_task(pool.chat([], preferred="a"))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(calls), 1)
        self.assertEqual(pool.cooldown_snapshot(), {})
        self.assertTrue(client.is_closed)

    async def test_local_trickling_http_response_has_wall_clock_limit(self):
        self.cfg.update(tiers=[["a"]], attempt_timeout_seconds=0.3, total_budget_seconds=0.16)
        connections = set()
        async def serve(reader, writer):
            connections.add(asyncio.current_task())
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                length = next(int(line.split(b":", 1)[1]) for line in headers.splitlines()
                              if line.lower().startswith(b"content-length:"))
                await reader.readexactly(length)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
                for _ in range(100):
                    writer.write(b" ")
                    await writer.drain()
                    await asyncio.sleep(0.02)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                connections.discard(asyncio.current_task())
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        self.cfg["base_url"] = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        start = time.monotonic()
        try:
            with self.assertRaises(pool.LLMPoolExhausted):
                await pool.chat([], preferred="a")
            self.assertLess(time.monotonic() - start, 0.6)
        finally:
            server.close()
            await server.wait_closed()
            tasks = list(connections)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.children = []
        self.spawn = asyncio.create_subprocess_exec

    async def asyncTearDown(self):
        for child in self.children:
            if child.returncode is None:
                child.kill()
            await child.wait()
        if process_guard._reaping:
            await asyncio.gather(*list(process_guard._reaping), return_exceptions=True)

    async def capture(self, *args, **kwargs):
        child = await self.spawn(*args, **kwargs)
        self.children.append(child)
        return child

    async def test_success(self):
        code, out, err = await process_guard.run_process(sys.executable, "-c", "print('OK')")
        self.assertEqual((code, out.strip(), err), (0, b"OK", b""))

    async def test_timeout_reaps_real_child(self):
        with patch.object(asyncio, "create_subprocess_exec", side_effect=self.capture):
            with self.assertRaises(asyncio.TimeoutError):
                await process_guard.run_process(sys.executable, "-c", "import time; time.sleep(10)",
                                                timeout=0.3, cleanup_seconds=0.1)
        self.assertIsNotNone(self.children[0].returncode)

    async def test_cancel_while_running_or_creating(self):
        for delayed in (False, True):
            with self.subTest(delayed=delayed):
                entered, release = asyncio.Event(), asyncio.Event()
                async def capture(*args, **kwargs):
                    child = await self.capture(*args, **kwargs)
                    entered.set()
                    if delayed:
                        await release.wait()
                    return child
                with patch.object(asyncio, "create_subprocess_exec", side_effect=capture):
                    task = asyncio.create_task(process_guard.run_process(
                        sys.executable, "-c", "import time; time.sleep(10)", timeout=1))
                    await entered.wait()
                    if not delayed:
                        await asyncio.sleep(0.02)
                    task.cancel()
                    release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertIsNotNone(self.children[-1].returncode)

    async def test_timeout_during_process_creation(self):
        async def delayed(*args, **kwargs):
            child = await self.capture(*args, **kwargs)
            await asyncio.sleep(0.15)
            return child
        with patch.object(asyncio, "create_subprocess_exec", side_effect=delayed):
            with self.assertRaises(asyncio.TimeoutError):
                await process_guard.run_process(sys.executable, "-c", "import time; time.sleep(10)",
                                                timeout=0.24, cleanup_seconds=0.12)
        self.assertIsNotNone(self.children[-1].returncode)

    async def test_engine_cancel_never_calls_fallback(self):
        from scripts.decision_engine import DecisionEngine
        engine = DecisionEngine()
        with patch.object(engine, "_call_model_script", AsyncMock(side_effect=asyncio.CancelledError)) as call, \
                patch("scripts.decision_engine.chat", AsyncMock(side_effect=asyncio.CancelledError)) as direct:
            with self.assertRaises(asyncio.CancelledError):
                await engine._generate_reply_with_failover("offline")
        self.assertEqual(call.await_count + direct.await_count, 1)

    async def test_cancel_at_creation_or_communication_completion_is_not_swallowed(self):
        for phase in ("creating", "communicating"):
            with self.subTest(phase=phase):
                entered = asyncio.Event()
                observed = []

                class Child:
                    returncode = None
                    def kill(self):
                        self.returncode = -1
                    async def communicate(self, input=None):
                        if phase == "communicating":
                            entered.set()  # 取消与 communicate 结果完成处于同一轮事件循环
                        self.returncode = 0
                        return b"OK", b""
                    async def wait(self):
                        return self.returncode

                async def create(*_, **__):
                    if phase == "creating":
                        entered.set()
                    return Child()

                token = process_guard.cleanup_observer.set(observed.append)
                try:
                    with patch.object(asyncio, "create_subprocess_exec", side_effect=create):
                        task = asyncio.create_task(process_guard.run_process("offline", timeout=1))
                        await entered.wait()
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 0.5)
                    self.assertEqual(len(observed), 1)
                    self.assertTrue(observed[0].done())
                finally:
                    process_guard.cleanup_observer.reset(token)

    async def test_cleanup_observer_failure_does_not_interrupt_reaping(self):
        token = process_guard.cleanup_observer.set(Mock(side_effect=ValueError("offline")))
        try:
            with patch.object(asyncio, "create_subprocess_exec", side_effect=self.capture):
                with self.assertRaises(asyncio.TimeoutError):
                    await process_guard.run_process(sys.executable, "-c", "import time;time.sleep(10)",
                                                    timeout=0.2, cleanup_seconds=0.1)
            self.assertIsNotNone(self.children[0].returncode)
        finally:
            process_guard.cleanup_observer.reset(token)


if __name__ == "__main__":
    unittest.main()
