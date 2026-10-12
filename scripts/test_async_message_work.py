# -*- coding: utf-8 -*-
"""异步工作离线验证，不加载真实分类模型。"""

import asyncio
import importlib
import json
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import numpy as np

from scripts import async_work as work, image_utils, vision
from scripts.decision_engine import DecisionEngine


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        work.start_workers()

    async def asyncTearDown(self):
        await work.close_workers()

    async def test_cancel_keeps_running_slot_and_loop_responsive(self):
        worker = work.BoundedWorker(1, "offline")
        entered, release = threading.Event(), threading.Event()
        def blocked():
            entered.set()
            release.wait(2)
            return 1
        task = asyncio.create_task(worker.run(blocked, timeout=1))
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
            heartbeat = asyncio.Event()
            asyncio.get_running_loop().call_soon(heartbeat.set)
            await asyncio.wait_for(heartbeat.wait(), 0.2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(len(worker.futures), 1)
            self.assertIsNone(await worker.run(lambda: 2))
        finally:
            release.set()
            await worker.close()
        self.assertFalse(worker.futures)

    async def test_timeout_and_pending_capacity_are_bounded(self):
        worker = work.BoundedWorker(1, "offline")
        release = threading.Event()
        try:
            self.assertIsNone(await worker.run(lambda: release.wait(2), timeout=0.02))
            second = asyncio.create_task(worker.run(lambda: 2, pending=1, timeout=1))
            await asyncio.sleep(0)
            self.assertEqual(len(worker.futures), 2)
            self.assertIsNone(await worker.run(lambda: 3, pending=1))
            release.set()
            self.assertEqual(await second, 2)
        finally:
            release.set()
            await worker.close()

    async def test_classification_does_not_block_event_loop(self):
        engine = DecisionEngine()
        entered, release = threading.Event(), threading.Event()
        def classify(*_):
            entered.set()
            release.wait(2)
            return "需要回复", 0.9
        engine._classifier = Mock(classify=classify)
        task = asyncio.create_task(engine._classify_needs_reply("测试消息"))
        try:
            await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
            self.assertFalse(task.done())
            await asyncio.wait_for(asyncio.sleep(0.01), 0.2)
        finally:
            release.set()
        self.assertEqual(await task, ("需要回复", 0.9))

    async def test_small_context_does_not_load_classifier(self):
        engine = DecisionEngine()
        with patch.object(engine, "_get_classifier", AsyncMock()) as get:
            self.assertEqual(await engine._detect_topic_switch("g"), 0)
        get.assert_not_called()

    async def test_slow_initialization_has_no_lock_wait_queue_and_is_reused_after_cancel(self):
        engine = DecisionEngine()
        entered, release = threading.Event(), threading.Event()
        classifier = Mock()
        def create():
            entered.set()
            release.wait(2)
            engine._classifier = classifier
            return classifier
        with patch.object(engine, "_create_classifier", side_effect=create) as factory:
            task = asyncio.create_task(engine._get_classifier())
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
                self.assertIsNone(await engine._get_classifier())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            await work.close_workers()
            self.assertIs(await engine._get_classifier(), classifier)
            self.assertEqual(factory.call_count, 1)

    async def test_slow_image_encoder_runs_off_loop(self):
        entered, release = threading.Event(), threading.Event()
        def encode(_):
            entered.set()
            release.wait(2)
            return "encoded"
        with patch.object(image_utils, "to_data_url", side_effect=encode):
            task = asyncio.create_task(image_utils.to_data_url_async(b"offline"))
            try:
                await asyncio.wait_for(asyncio.to_thread(entered.wait), 1)
                self.assertFalse(task.done())
                await asyncio.wait_for(asyncio.sleep(0), 0.2)
            finally:
                release.set()
            self.assertEqual(await task, "encoded")

    async def test_byte_helper_uses_async_image_processing(self):
        with patch.object(image_utils, "to_data_url_async", AsyncMock(return_value="data:image/jpeg;base64,eA==")) as encode, \
                patch.object(vision, "describe", AsyncMock(return_value=(True, "测试图"))):
            self.assertEqual(await vision.describe_image_bytes(b"offline"), (True, "测试图"))
        encode.assert_awaited_once_with(b"offline")
        self.assertIsNone(await image_utils.to_data_url_async(b"broken image"))

    async def test_config_reload_and_invalid_file_keep_last_good(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "worker.json"
            with patch.multiple(work, CONFIG_FILE=path, _signature=None, _config=dict(work.DEFAULTS)):
                self.assertEqual(work.settings(), work.DEFAULTS)
                path.write_text(json.dumps({"image_workers": 3}), encoding="utf-8")
                self.assertEqual(work.settings()["image_workers"], 3)
                path.write_text("[]", encoding="utf-8")
                self.assertEqual(work.settings()["image_workers"], 3)
                path.write_text(json.dumps({"image_workers": True}), encoding="utf-8")
                self.assertEqual(work.settings()["image_workers"], 3)

    async def test_classifier_label_vectors_are_cached_and_bounded(self):
        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = Mock()
        with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
            module = importlib.import_module("scripts.zero_shot_classifier")
        model = Mock()
        model.encode.side_effect = lambda values, **kwargs: np.ones((len(values), 2))
        classifier = module.ZeroShotClassifier()
        classifier._model = model
        classifier.classify("甲", ["需要回复", "不需要回复"])
        classifier.classify("乙", ["需要回复", "不需要回复"])
        self.assertEqual(model.encode.call_count, 3)  # 两条消息 + 一次标签编码
        for n in range(30):
            classifier._encode_labels([str(n)])
        self.assertLessEqual(len(classifier._label_cache), 16)


class DownloadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        work.start_workers()

    async def asyncTearDown(self):
        await work.close_workers()

    async def test_shutdown_cancels_download_and_closes_its_client(self):
        entered = asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await asyncio.Event().wait()
                yield b"offline"
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=Stream())))
        with patch.object(image_utils.httpx, "AsyncClient", return_value=client):
            task = asyncio.create_task(image_utils.fetch_image_async({"url": "http://offline"}))
            await asyncio.wait_for(entered.wait(), 1)
            await work.close_workers()
            self.assertTrue(task.cancelled())
            self.assertTrue(client.is_closed)
            self.assertEqual(work._downloads, 0)
            self.assertFalse(work._download_tasks)

    async def test_slow_download_allows_other_coroutines_and_releases_on_cancel(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                entered.set()
                await release.wait()
                yield b"offline"
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, stream=Stream())))
        with patch.object(image_utils.httpx, "AsyncClient", return_value=client):
            task = asyncio.create_task(image_utils.fetch_image_async({"url": "http://offline/image"}))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(asyncio.sleep(0.01), 0.2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(work._downloads, 0)
        self.assertTrue(client.is_closed)

    async def test_stream_size_cap_and_preflight(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 20)))
        with patch.object(image_utils.httpx, "AsyncClient", return_value=client) as factory:
            self.assertIsNone(await image_utils.fetch_image_async({"url": "http://offline", "file_size": 21}, max_bytes=10))
            factory.assert_not_called()
            self.assertIsNone(await image_utils.fetch_image_async({"url": "http://offline"}, max_bytes=10))
        self.assertTrue(client.is_closed)

    async def test_no_download_slot_does_not_create_client(self):
        with patch.object(work, "_downloads", work.settings()["download_concurrency"]), \
                patch.object(image_utils.httpx, "AsyncClient") as factory:
            self.assertIsNone(await image_utils.fetch_image_async({"url": "http://offline"}))
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
