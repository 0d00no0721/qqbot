# -*- coding: utf-8 -*-
"""图片预取与隔离进程名额回归；仅临时数据、本地 HTTP 桩与事件屏障。"""

import asyncio
import gc
import json
import os
import sys
import tempfile
import threading
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts import image_utils, request_limits as rl
from scripts.image_prefetch import AttachmentBudget, ImagePrefetch
from scripts.message_queue import MessageQueue
from scripts.test_message_queue import Config, Clock, message, settle


class PrefetchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Config(max_prefetch_bytes=32)
        self.budget = AttachmentBudget(self.cfg)
        self.batches = []

    def batch(self):
        batch = ImagePrefetch(self.budget)
        self.batches.append(batch)
        return batch

    async def asyncTearDown(self):
        tasks = [batch.close() for batch in self.batches]
        await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)
        gc.collect()
        self.assertEqual(self.budget.used, 0)

    async def test_global_budget_includes_downloads_and_copy_before_completion(self):
        release = asyncio.Event()
        calls = []

        async def fetch(meta, **_):
            calls.append(meta["group"])
            await release.wait()
            return b"12345678"

        with patch.object(image_utils, "fetch_image_async", fetch, create=True), \
                patch.object(image_utils, "DEFAULT_MAX_BYTES", 8):
            for group in range(3):
                self.batch().start([{"group": group}])
            await settle()
            self.assertEqual(calls, [0, 1])
            self.assertEqual(self.budget.used, 32)  # 两份下载缓冲 + 两份转换预留
            self.cfg.values["max_prefetch_bytes"] = 8
            self.batch().start([{"group": 4}])
            await settle()
            self.assertEqual(calls, [0, 1])  # 调小只拒绝新增，不回收进行中的下载
            release.set()
            await asyncio.gather(*(b.task for b in self.batches))
            self.assertEqual(self.budget.used, 16)

    async def test_cancelled_download_keeps_reservation_until_downloader_exits(self):
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def fetch(*_, **__):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()  # 模拟连接关闭尚未完成
                raise

        with patch.object(image_utils, "fetch_image_async", fetch, create=True), \
                patch.object(image_utils, "DEFAULT_MAX_BYTES", 8):
            batch = self.batch()
            batch.start([{}])
            await entered.wait()
            task = batch.close()
            await cancelled.wait()
            self.assertEqual(self.budget.used, 16)
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(self.budget.used, 0)

    async def test_failed_download_and_cancel_before_start_release_all_bytes(self):
        with patch.object(image_utils, "fetch_image_async", AsyncMock(return_value=None), create=True), \
                patch.object(image_utils, "DEFAULT_MAX_BYTES", 8):
            batch = self.batch()
            batch.start([{}])
            await batch.task
            self.assertEqual(self.budget.used, 0)
            other = self.batch()
            other.start([{}])
            await asyncio.gather(other.close(), return_exceptions=True)
            self.assertEqual(self.budget.used, 0)

    async def test_expiry_coalescing_disconnect_and_close_dispose_pending_attachments(self):
        clock, ws = Clock(), object()
        queue = MessageQueue(AsyncMock(), Config(max_model_calls=1), clock)
        blocked = asyncio.Event()
        queue.submit(ws, message(0), blocked.wait, True)
        try:
            with patch.object(image_utils, "fetch_image_async", AsyncMock(return_value=b"test"), create=True), \
                    patch.object(image_utils, "DEFAULT_MAX_BYTES", 8):
                for mid in (1, 2):
                    batch = self.batch()
                    self.assertTrue(queue.submit(ws, message(mid), AsyncMock(), cleanup=batch.close))
                    batch.start([{}])
                    await batch.task
                self.assertTrue(self.batches[0].closed)  # 闲聊合并
                self.assertEqual(self.budget.used, 4)
                clock.now += 16
                queue.tick()
                self.assertEqual(self.budget.used, 0)
                for mid in (3, 4):
                    batch = self.batch()
                    queue.submit(ws, message(mid), AsyncMock(), True, cleanup=batch.close)
                    batch.start([{}])
                    await batch.task
                self.assertEqual(self.budget.used, 8)
                await asyncio.gather(*queue.disconnect(ws), return_exceptions=True)
                self.assertEqual(self.budget.used, 0)
        finally:
            await queue.close()
        self.assertFalse(queue.cleanup_tasks)

    async def test_idle_and_rejected_job_do_not_start_download_tasks(self):
        batch = self.batch()
        before = set(asyncio.all_tasks())
        batch.start([])
        self.assertIsNone(batch.task)
        self.assertEqual(set(asyncio.all_tasks()), before)
        with self.assertRaises(ValueError):
            rl.LimitsConfig.validate({"max_prefetch_bytes": 8}, rl.DEFAULTS, group=True)


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import reverse_bot as bot
        from scripts import command_handler, image_cache
        from scripts.commands import _state
        self.bot = bot
        self.ws = object()
        self.queue = MessageQueue(AsyncMock(), Config(max_model_calls=1, max_pending_per_group=1))
        self.records, self.contexts = [], []
        self.engine = Mock()
        self.engine.get_all_character_aliases.return_value = {"小助手": "1"}
        self.engine.should_reply = AsyncMock(return_value=(False, ""))

        def record(gid, uid, nick, text, **_):
            entry = {"content": text}
            self.contexts.append(entry)
            return entry

        self.engine.add_message.side_effect = record
        self.patches = [
            patch.multiple(bot, message_queue=self.queue, decision_engine=self.engine,
                PROFILE_ENABLED=True, get_profile=lambda *_: "", bot_logger=Mock(),
                group_histories={"1": deque(maxlen=50)},
                record_message=lambda *args: self.records.append(args),
                handle_command=AsyncMock(return_value=False),
                _feature_on=lambda gid, key: key in {"commands", "decision_engine", "profile_record", "vision"}),
            patch.object(command_handler, "processed_msg_ids", set()),
            patch.object(image_cache, "remember"),
            patch.object(_state, "vision_usage", __import__("collections").defaultdict(list)),
            patch.object(image_cache, "seen_hash", return_value=False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.queue.close()

    async def test_queued_image_downloads_immediately_rejected_chat_still_records_once(self):
        from scripts import image_cache
        release, downloaded = asyncio.Event(), asyncio.Event()
        captured = []

        async def run(*args):
            captured.append(args)
            await release.wait()

        async def fetch(*_, **__):
            downloaded.set()
            return b"cached image"

        mention = f"[CQ:at,qq={self.bot.BOT_QQ}] "
        with patch.object(self.bot, "_run_chat_message", run), \
                patch.object(image_utils, "fetch_image_async", AsyncMock(side_effect=fetch), create=True) as get, \
                patch.object(image_cache, "select_candidates", return_value=([], "本消息")) as select:
            await self.bot.handle_message(self.ws, message(1, raw=mention + "第一条"))
            await settle()
            select.return_value = ([{"url": "https://offline/short-lived", "file": "ABC"}], "本消息")
            await self.bot.handle_message(self.ws, message(2, raw=mention + "看图"))
            await asyncio.wait_for(downloaded.wait(), 1)
            self.assertEqual(len(captured), 1)  # 上条聊天未完成，下一条已下载
            await self.bot.handle_message(self.ws, message(3, raw=mention + "队满也记录"))
            await self.bot.handle_message(self.ws, message(3, raw=mention + "队满也记录"))
            await settle()
            self.assertEqual([row[2] for row in self.records], ["第一条", "看图", "队满也记录"])
            self.assertEqual(len(self.contexts), 3)
            self.assertEqual(get.await_count, 1)
            self.assertEqual(self.queue.pending_count, 1)
            release.set()
            await settle()
            self.assertEqual(len(captured), 2)

    async def test_filters_hash_and_hourly_quota_run_before_any_prefetch(self):
        from scripts import image_cache
        from scripts.commands import _state
        batch = Mock()
        sticker = {"file": "sticker", "sub_type": 1}
        photo = {"file": "photo"}
        with patch.object(image_cache, "image_hash", side_effect=lambda meta: meta["file"]), \
                patch.object(image_cache, "seen_hash", side_effect=[False, True]) as seen, \
                patch.object(_state, "vision_rate_ok", side_effect=[(True, 1), (True, 2), (False, 10)]):
            self.bot._prefetch_triggered_image(batch, "1", ([sticker, photo], "本消息"))
            batch.start.assert_called_with([photo])
            self.bot._prefetch_triggered_image(batch, "1", ([photo], "本消息"))
            batch.start.assert_called_with([])
            self.bot._prefetch_triggered_image(batch, "1", ([photo], "本消息"))
            self.assertEqual(batch.start.call_count, 2)
            self.assertEqual(seen.call_count, 2)
        with patch.object(image_cache, "seen_hash", return_value=False):
            self.bot._prefetch_triggered_image(batch, "1", ([sticker], "引用"))
            batch.start.assert_called_with([sticker])

    async def test_ordinary_unmentioned_image_does_not_download_alias_does(self):
        from scripts import image_cache
        with patch.object(self.bot, "_run_chat_message", AsyncMock()), \
                patch.object(self.bot, "_prefetch_triggered_image") as prepare, \
                patch.object(image_cache, "select_candidates", return_value=([{"file": "p"}], "本消息")):
            await self.bot.handle_message(self.ws, message(1, raw="普通文字 [CQ:image,file=p]"))
            prepare.assert_not_called()
            await self.bot.handle_message(self.ws, message(2, raw="小助手看看 [CQ:image,file=p]"))
            prepare.assert_called_once()
            await settle()


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_author_reply_filters_and_group_exact_dedup_are_preserved(self):
        from scripts.decision_engine import DecisionEngine
        engine = DecisionEngine()
        self.assertFalse(engine._validate_content("换个话题吧")[0])
        self.assertFalse(engine._validate_content("好的")[0])
        self.assertTrue(engine._validate_content("你好呀")[0])
        with patch.object(engine, "_generate_reply_async", AsyncMock(return_value="你好呀")) as generate:
            first = await engine.should_reply("第一个问题", "1", "offline", is_at_bot=True)
            second = await engine.should_reply("第二个问题", "2", "offline", is_at_bot=True)
        self.assertEqual(first, (True, "你好呀"))
        self.assertEqual(second, (False, None))
        self.assertEqual(generate.await_count, 2)


try:
    from scripts import async_work, process_guard
    HAS_DEPENDENCIES = hasattr(image_utils, "fetch_image_async")
except ImportError:
    HAS_DEPENDENCIES = False


@unittest.skipUnless(HAS_DEPENDENCIES, "组合验证需先叠加 #2、#3；不包含 #4")
class DependencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async_work.start_workers()

    async def asyncTearDown(self):
        await async_work.close_workers()
        if process_guard._reaping:
            await asyncio.gather(*list(process_guard._reaping), return_exceptions=True)
        gc.collect()

    async def test_cancelled_image_worker_keeps_attachment_budget_until_thread_finishes(self):
        from scripts import vision
        entered, release = threading.Event(), threading.Event()
        budget = AttachmentBudget(Config(max_prefetch_bytes=32))
        batch = ImagePrefetch(budget)

        def encode(_):
            entered.set()
            release.wait(3)
            return "data:image/jpeg;base64,eA=="

        with patch.object(image_utils, "fetch_image_async", AsyncMock(return_value=b"test")), \
                patch.object(image_utils, "DEFAULT_MAX_BYTES", 8), \
                patch.object(image_utils, "to_data_url", side_effect=encode), \
                patch.object(vision, "describe", AsyncMock(return_value=(True, "离线图片"))):
            batch.start([{}])
            await batch.task
            task = asyncio.create_task(batch.describe("hint"))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                batch.close()
                self.assertEqual(budget.used, 4)
            finally:
                release.set()
                await async_work.close_workers()
            del task
            gc.collect()
            self.assertEqual(budget.used, 0)

    async def test_cached_bytes_survive_url_expiry_without_redownload(self):
        import httpx
        from scripts import vision
        expired, requests = False, []

        def serve(request):
            requests.append(str(request.url))
            return httpx.Response(403 if expired else 200, content=b"cached image")

        client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
        budget = AttachmentBudget(Config())
        batch = ImagePrefetch(budget)
        with patch.object(image_utils.httpx, "AsyncClient", return_value=client), \
                patch.object(image_utils, "to_data_url", return_value="data:image/jpeg;base64,eA==") as encode, \
                patch.object(vision, "describe", AsyncMock(return_value=(True, "离线图片"))):
            batch.start([{"url": "http://offline/temporary"}])
            await batch.task
            expired = True
            self.assertEqual(await batch.describe("看图"), ["[图片: 离线图片]"])
            self.assertEqual(len(requests), 1)
            encode.assert_called_once_with(b"cached image")
            self.assertTrue(client.is_closed)
            batch.close()
        self.assertEqual(budget.used, 0)

    async def test_single_slot_vision_then_text_and_command_never_nest_or_overlap(self):
        from scripts import llm_pool
        from scripts.decision_engine import DecisionEngine
        engine = DecisionEngine()
        gate = rl.ModelGate(Config(max_model_calls=1))
        entered, release = asyncio.Event(), asyncio.Event()
        stages = []

        @rl.limit_model_calls
        async def vision():
            stages.append("vision")
            entered.set()
            await release.wait()

        async def script(*_, **__):
            self.assertEqual(gate.active, 1)
            stages.append("text")
            return "这是正常回答。"

        @rl.limit_model_calls
        async def command():
            stages.append("command")

        async def chat():
            await vision()
            return await engine._generate_reply_with_failover("offline")

        with patch.object(rl, "model_gate", gate), patch.object(engine, "_call_model_script", side_effect=script):
            task = asyncio.create_task(chat())
            await entered.wait()
            other = asyncio.create_task(command())
            await settle()
            self.assertEqual(stages, ["vision"])
            release.set()
            answer, _ = await asyncio.wait_for(asyncio.gather(task, other), 2)
            self.assertEqual(answer, "这是正常回答。")
            self.assertEqual(stages, ["vision", "command", "text"])
            self.assertEqual(gate.active, 0)

    async def test_primary_fallback_use_distinct_pids_and_unchanged_json_interface(self):
        from scripts.decision_engine import DecisionEngine
        engine = DecisionEngine()
        pids, requests = [], []
        spawn = asyncio.create_subprocess_exec

        async def serve(reader, writer):
            try:
                header = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
                size = next(int(line.split(":", 1)[1]) for line in header.splitlines()
                            if line.lower().startswith("content-length:"))
                payload = json.loads(await reader.readexactly(size))
                requests.append(payload["model"])
                body = json.dumps({"choices": [{"message": {"content": "换个话题吧"}}]}).encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                             + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async def capture(*args, **kwargs):
            proc = await spawn(*args, **kwargs)
            pids.append(proc)
            return proc

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1"
            with tempfile.TemporaryDirectory() as temp:
                config = Path(temp) / "pool.json"
                config.write_text(json.dumps({"tiers": [[f"m{i}" for i in range(9)]]}), encoding="utf-8")
                for name in ("primary", "fallback"):
                    script = Path(temp) / (name + ".py")
                    # 独立进程运行原脚本的真实 stdin/stdout 入口，只替换配置和本地网关。
                    script.write_text('import sys\nfrom pathlib import Path\n'
                        f'sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n'
                        'from scripts import llm_pool\n'
                        f'llm_pool.CONFIG_PATH=Path({str(config)!r})\n'
                        f'from scripts.model_{name} import main\nmain()\n', encoding="utf-8")
                    setattr(engine, f"_{name}_script", str(script))
                with patch.object(engine, "_format_context", AsyncMock(return_value="offline")), \
                        patch.object(engine, "_load_character", return_value="offline"), \
                        patch.object(engine, "get_active_model", return_value={
                            "model": "m0", "api_base_url": url, "api_key": "offline"}), \
                        patch.dict(os.environ, {"LLM_POOL_LOG_PATH": str(Path(temp) / "audit.jsonl"),
                                               "LLM_POOL_DISABLE": "0", "PYTHONIOENCODING": "utf-8"}), \
                        patch.object(asyncio, "create_subprocess_exec", side_effect=capture):
                    answer = await engine._generate_reply_with_failover("offline", target={
                        "uid": "23", "nickname": "测试", "content": "离线问题"})
                self.assertEqual(answer, "")  # 作者的硬拦截仍在
                self.assertEqual(requests, ["m0", "m0"])  # 不扩成遍历 9 个内容无效模型
                self.assertEqual(len(pids), 2)
                self.assertNotEqual(pids[0].pid, pids[1].pid)
                self.assertTrue(all(proc.returncode is not None for proc in pids))
        finally:
            server.close()
            await server.wait_closed()

    async def test_running_process_cancel_never_calls_fallback_and_releases_after_exit(self):
        from scripts.decision_engine import DecisionEngine
        engine = DecisionEngine()
        gate = rl.ModelGate(Config(max_model_calls=1))
        entered = asyncio.Event()
        children = []
        spawn = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            entered.set()
            return child

        with tempfile.TemporaryDirectory() as temp:
            script = Path(temp) / "sleep.py"
            script.write_text("import time; time.sleep(20)", encoding="utf-8")
            engine._primary_script = engine._fallback_script = str(script)
            with patch.object(rl, "model_gate", gate), \
                    patch.object(asyncio, "create_subprocess_exec", side_effect=capture), \
                    patch.object(engine, "_format_context", AsyncMock(return_value="offline")), \
                    patch.object(engine, "_load_character", return_value="offline"):
                task = asyncio.create_task(engine._generate_reply_with_failover("offline"))
                await entered.wait()
                self.assertEqual(gate.active, 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].returncode)
                self.assertEqual(gate.active, 0)

    async def test_late_process_creation_keeps_parent_slot_and_stops_extra_script(self):
        gate = rl.ModelGate(Config(max_model_calls=1))
        entered, release = asyncio.Event(), asyncio.Event()
        children = []
        spawn = asyncio.create_subprocess_exec

        async def delayed(*args, **kwargs):
            child = await spawn(*args, **kwargs)
            children.append(child)
            entered.set()
            await release.wait()
            return child

        @rl.track_model_script
        async def script():
            try:
                await process_guard.run_process(sys.executable, "-c", "import time;time.sleep(10)",
                                                timeout=0.12, cleanup_seconds=0.04)
            except asyncio.TimeoutError:
                return ""

        @rl.limit_model_calls
        async def chain():
            await script()
            await script()

        with patch.object(rl, "model_gate", gate), \
                patch.object(asyncio, "create_subprocess_exec", side_effect=delayed):
            task = asyncio.create_task(chain())
            try:
                await entered.wait()
                with self.assertRaises(rl.ModelAdmissionError):
                    await task
                self.assertEqual(gate.active, 1)
                self.assertEqual(len(children), 1)
            finally:
                release.set()
                await asyncio.gather(*list(process_guard._reaping), return_exceptions=True)
                await settle()
            self.assertIsNotNone(children[0].returncode)
            self.assertEqual(gate.active, 0)


if __name__ == "__main__":
    unittest.main()
