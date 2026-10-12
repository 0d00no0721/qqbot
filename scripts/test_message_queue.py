# -*- coding: utf-8 -*-
"""消息队列离线验证；只用模拟消息、临时配置和事件屏障。"""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts.message_queue import MessageQueue
from scripts import request_limits as rl


class Config:
    def __init__(self, **values):
        self.values = {**rl.DEFAULTS, **values}

    def get(self, gid=None):
        return dict(self.values)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def message(mid=1, gid=1, uid=2, raw="你好"):
    return {"post_type": "message", "message_type": "group", "group_id": gid,
            "user_id": uid, "message_id": mid, "sender": {"nickname": str(uid)},
            "raw_message": raw}


async def settle():
    # 让 task、完成回调和提示任务各走一轮，不依赖墙钟延时。
    for _ in range(8):
        await asyncio.sleep(0)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Config(max_model_calls=1)
        self.clock = Clock()
        self.send = AsyncMock()
        self.queue = MessageQueue(self.send, self.cfg, self.clock)
        self.ws = object()
        self.started = []
        self.barriers = {}

    async def asyncTearDown(self):
        await self.queue.close()

    def submit(self, mid, gid=1, explicit=True, ws=None):
        barrier = self.barriers.setdefault(mid, asyncio.Event())

        async def run():
            self.started.append(mid)
            await barrier.wait()
        return self.queue.submit(ws or self.ws, message(mid, gid), run, explicit)

    async def test_twenty_explicit_requests_admit_one_active_and_three_waiting(self):
        accepted = [self.submit(i) for i in range(20)]
        await settle()
        self.assertEqual(sum(accepted), 4)
        self.assertEqual(self.started, [0])
        self.assertEqual(self.queue.pending_count, 3)
        self.assertEqual(self.send.await_count, 1)  # 同群同用户拒绝提示冷却
        for mid in range(4):
            self.barriers[mid].set()
            await settle()
        self.assertEqual(self.started, [0, 1, 2, 3])
        self.assertFalse(self.queue.active)
        self.assertIsNone(self.queue.timer)

    async def test_groups_rotate_and_casual_candidates_coalesce(self):
        self.submit(1)
        self.submit(2)
        self.submit(3)
        self.submit(4, gid=2)
        self.submit(5, explicit=False)
        self.submit(6, explicit=False)
        self.assertEqual(self.queue.pending_count, 4)
        for mid in (1, 2, 4, 3, 6):
            await settle()
            self.assertEqual(self.started[-1], mid)
            self.barriers[mid].set()
        await settle()
        self.assertEqual(self.started, [1, 2, 4, 3, 6])
        self.assertNotIn(5, self.started)

    async def test_wait_notice_once_and_old_queue_deadline_does_not_cancel_running(self):
        self.submit(1)
        self.submit(2)
        self.clock.now += 11
        self.queue.tick()
        self.queue.tick()
        await settle()
        self.assertEqual(self.send.await_count, 1)
        self.assertEqual(self.send.call_args.args[2], self.cfg.values["wait_message"])
        self.clock.now = 279
        self.barriers[1].set()
        await settle()
        self.assertEqual(self.started, [1, 2])
        self.clock.now = 1000
        self.queue.tick()
        self.assertEqual(len(self.queue.active), 1)
        self.barriers[2].set()
        await settle()
        self.assertFalse(self.queue.active)

    async def test_expiry_global_capacity_and_config_shrink_preserve_old_deadline(self):
        self.cfg.values.update(max_pending=2)
        self.submit(1)
        self.submit(2)
        self.submit(3, gid=2)
        self.assertFalse(self.submit(4, gid=3))
        old = self.queue.pending["1"][0]
        self.cfg.values.update(queue_wait_seconds=1, max_pending=1, max_pending_per_group=0)
        self.clock.now += 2
        self.queue.tick()
        self.assertEqual(old.deadline, 280)
        self.assertEqual(self.queue.pending_count, 2)
        self.clock.now = 281
        self.queue.tick()
        await settle()
        self.assertEqual(self.queue.pending_count, 0)
        self.assertEqual(self.started, [1])
        self.assertIn(self.cfg.values["expired_message"], [call.args[2] for call in self.send.call_args_list])

    async def test_casual_expiry_is_silent_and_disconnect_only_cancels_its_connection(self):
        other = object()
        self.submit(1)
        self.submit(2, explicit=False)
        self.submit(3, gid=2, ws=other)
        self.clock.now += 16
        self.queue.tick()
        await settle()
        self.assertFalse(any(not job.explicit for jobs in self.queue.pending.values() for job in jobs))
        await asyncio.gather(*self.queue.disconnect(self.ws), return_exceptions=True)
        await settle()
        self.assertEqual(self.started, [1, 3])
        self.assertEqual(self.queue.groups_active, {"2"})
        await self.queue.close()
        self.assertFalse(self.queue.active)
        self.assertFalse(self.queue.pending)
        self.assertIsNone(self.queue.timer)

    async def test_idle_has_no_tasks_or_timer_and_notices_can_be_disabled(self):
        before = set(asyncio.all_tasks())
        self.clock.now += 1000
        self.queue.tick()
        self.assertIsNone(self.queue.timer)
        self.assertEqual(set(asyncio.all_tasks()), before)
        self.cfg.values.update(notices_enabled=False)
        self.submit(1)
        self.submit(2)
        self.clock.now += 12
        self.queue.tick()
        await settle()
        self.send.assert_not_called()
        # 禁用提示不能留下到期时间为过去的零延迟回调。
        self.assertGreater(self.queue.timer.when(), asyncio.get_running_loop().time())


class GateTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_expiry_cancel_and_handoff_release_every_slot(self):
        cfg = Config(max_model_calls=1, max_pending=1)
        gate = rl.ModelGate(cfg)
        await gate.acquire(rl.RequestState())
        waiter = asyncio.create_task(gate.acquire(rl.RequestState()))
        await settle()
        with self.assertRaises(rl.ModelAdmissionError):
            await gate.acquire(rl.RequestState())
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gate.active, 1)
        self.assertFalse(gate.waiters)
        state = rl.RequestState(remaining_wait=0.01)
        with self.assertRaises(rl.ModelAdmissionError):
            await gate.acquire(state)
        self.assertEqual(state.admission_reason, "expired")
        waiter = asyncio.create_task(gate.acquire(rl.RequestState()))
        await settle()
        gate.release()  # 已授予但尚未恢复协程时取消，不能泄漏名额。
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gate.active, 0)
        self.assertFalse(gate.waiters)

    async def test_model_wait_precedes_generation_budget_and_shared_calls_are_bounded(self):
        cfg = Config(max_model_calls=1)
        gate = rl.ModelGate(cfg)
        began, release = asyncio.Event(), asyncio.Event()
        executed = []

        @rl.limit_model_calls
        async def model(name):
            executed.append(name)
            if name == "a":
                began.set()
                await release.wait()
            else:
                # 生成自身 40ms 预算从拿到名额后开始，而非从排队开始。
                await asyncio.wait_for(asyncio.sleep(0.02), 0.04)
            rl.note_model_attempt()
            return name

        with patch.object(rl, "model_gate", gate):
            first = asyncio.create_task(model("a"))
            await began.wait()
            state = rl.RequestState(remaining_wait=0.5)
            token = rl.request_state.set(state)
            second = asyncio.create_task(model("b"))
            rl.request_state.reset(token)
            await settle()
            self.assertEqual(len(gate.waiters), 1)
            await asyncio.sleep(0.08)
            self.assertEqual(executed, ["a"])
            release.set()
            self.assertEqual(await asyncio.gather(first, second), ["a", "b"])
        self.assertGreaterEqual(state.model_wait, 0.05)
        self.assertEqual(state.attempts, 1)
        self.assertEqual(gate.active, 0)


class ConfigTests(unittest.TestCase):
    def test_defaults_groups_mtime_and_damaged_config(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "limits.json"
            cfg = rl.LimitsConfig(path)
            self.assertEqual(cfg.get(), rl.DEFAULTS)
            path.write_text(json.dumps({"defaults": {"max_pending": 9}, "groups": {
                "1": {"max_pending_per_group": 1, "queue_wait_seconds": 20}}}), encoding="utf-8")
            self.assertEqual(cfg.get(1)["max_pending"], 9)
            self.assertEqual(cfg.get(1)["queue_wait_seconds"], 20)
            self.assertEqual(cfg.get(2)["queue_wait_seconds"], 180)
            path.write_text("broken", encoding="utf-8")
            self.assertEqual(cfg.get(1)["queue_wait_seconds"], 20)
            for invalid in ({"max_model_calls": 0}, {"max_pending": float("nan")},
                            {"notices_enabled": 1}, {"wait_message": ""}):
                with self.assertRaises(ValueError):
                    cfg.validate(invalid, rl.DEFAULTS)


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import reverse_bot as bot
        self.bot = bot
        self.queue = MessageQueue(AsyncMock(), Config(max_model_calls=1))
        self.patches = [patch.object(bot, "message_queue", self.queue),
                        patch.object(bot, "_message_tasks", {}),
                        patch.object(bot, "_receipt_tails", {}),
                        patch.object(bot, "_receipt_pending", set()),
                        patch.object(bot, "bot_logger", Mock())]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        for task in list(self.bot._message_tasks):
            task.cancel()
        await asyncio.gather(*list(self.bot._message_tasks), return_exceptions=True)
        await self.queue.close()

    async def test_ingress_is_bounded_and_malformed_events_do_not_create_tasks(self):
        barrier = asyncio.Event()

        async def blocked(*_):
            await barrier.wait()
        with patch.object(self.bot, "_safe_handle_message", blocked), \
                patch.object(self.bot, "limits", Config(max_message_tasks=2)), \
                patch.object(self.bot, "_feature_on", return_value=False):
            self.assertFalse(self.bot._start_message(None, {}))
            self.assertTrue(self.bot._start_message(None, message(1)))
            self.assertTrue(self.bot._start_message(None, message(2)))
            self.assertFalse(self.bot._start_message(None, message(3)))
            self.assertEqual(len(self.bot._message_tasks), 2)
            barrier.set()
            await settle()
            self.assertFalse(self.bot._message_tasks)

    async def test_slow_voice_preserves_receipt_order_without_blocking_text_commands(self):
        from scripts.decision_engine import DecisionEngine
        from scripts import command_handler as commands
        engine = DecisionEngine()
        entered, release = asyncio.Event(), asyncio.Event()
        records = []
        mention = f"[CQ:at,qq={self.bot.BOT_QQ}] "

        async def transcribe(_):
            entered.set()
            await release.wait()
            return mention + "先到的语音"

        async def dispatch(ws, data, clean, raw):
            return clean == "帮助"

        with patch.multiple(self.bot, decision_engine=engine,
                _feature_on=lambda gid, key: key in {"commands", "decision_engine", "profile_record"},
                process_voice_message=transcribe, handle_command=AsyncMock(side_effect=dispatch),
                _run_chat_message=AsyncMock(), get_profile=lambda *_: "", PROFILE_ENABLED=True,
                record_message=lambda gid, uid, text: records.append(text)), \
                patch.object(commands, "processed_msg_ids", set()):
            self.bot._start_message(None, message(1, raw="[CQ:record,file=offline]"))
            await entered.wait()
            self.bot._start_message(None, message(2, raw=mention + "后到的文字"))
            self.bot._start_message(None, message(3, raw=mention + "帮助"))
            await settle()
            self.assertEqual(self.bot.handle_command.await_count, 2)
            self.assertFalse(records)
            release.set()
            await settle()
            self.assertEqual(records, ["先到的语音", "后到的文字"])
            self.assertEqual([m["content"] for m in engine._contexts["1"]._buffer], records)
            await settle()
            self.assertFalse(self.bot._receipt_pending)
            self.assertFalse(self.bot._receipt_tails)

    async def test_receive_records_once_freezes_target_and_commands_bypass_chat(self):
        from scripts.decision_engine import DecisionEngine
        from scripts import command_handler as commands, image_cache
        engine = DecisionEngine()
        started, release = asyncio.Event(), asyncio.Event()
        records, captured = [], []

        async def chat(*args):
            captured.append(args)
            started.set()
            await release.wait()

        async def dispatch(ws, data, clean, raw):
            return clean == "帮助"

        features = lambda gid, key: key in {"commands", "decision_engine", "vision", "profile_record"}
        with patch.multiple(self.bot, decision_engine=engine, _feature_on=features,
                _run_chat_message=chat, handle_command=AsyncMock(side_effect=dispatch),
                record_message=lambda *args: records.append(args), get_profile=lambda *_: "",
                PROFILE_ENABLED=True, group_histories={"1": __import__("collections").deque(maxlen=50)}), \
                patch.object(commands, "processed_msg_ids", set()), \
                patch.object(image_cache, "select_candidates", return_value=([{"url": "original"}], "引用")):
            mention = f"[CQ:at,qq={self.bot.BOT_QQ}] "
            first = message(1, raw=mention + "第一条")
            await self.bot.handle_message(None, first)
            await started.wait()
            second = message(2, uid=3, raw=mention + "第二条")
            await self.bot.handle_message(None, second)
            second["sender"]["nickname"] = "被改动的昵称"
            second["user_id"] = 999
            for mid in (3, 4):
                await self.bot.handle_message(None, message(mid, raw=f"闲聊{mid}"))
            await self.bot.handle_message(None, message(5, raw=mention + "帮助"))
            self.assertEqual(self.bot.handle_command.await_count, 3)
            self.assertEqual(len(records), 4)
            self.assertEqual([m["content"] for m in engine._contexts["1"]._buffer],
                             ["第一条", "第二条", "闲聊3", "闲聊4"])
            release.set()
            await settle()
            self.assertEqual(len(records), 4)
            self.assertEqual(captured[1][1]["user_id"], 3)
            self.assertEqual(captured[1][1]["sender"]["nickname"], "3")
            self.assertEqual(captured[1][-1], ([{"url": "original"}], "引用"))
            self.assertEqual(len(captured), 3)  # 第一条、第二条、最后的闲聊

    async def test_dedup_evicts_one_old_id_and_preserves_public_export(self):
        from scripts import command_handler as commands
        ids = set()
        with patch.object(commands, "processed_msg_ids", ids), \
                patch.object(commands, "PROCESSED_MSG_MAX", 3), \
                patch.object(self.bot, "_feature_on", return_value=False):
            for mid in range(5):
                await self.bot.handle_message(None, message(mid))
            self.assertEqual(ids, {2, 3, 4})
            await self.bot.handle_message(None, message(4))
            self.assertEqual(ids, {2, 3, 4})

    async def test_websocket_keeps_receiving_and_disconnect_releases_ingress(self):
        incoming = asyncio.Queue()
        received, notice = asyncio.Event(), asyncio.Event()

        class WebSocket:
            remote_address = "offline"
            def __aiter__(self):
                return self
            async def __anext__(self):
                value = await incoming.get()
                if value is None:
                    raise StopAsyncIteration
                return json.dumps(value)

        async def blocked(*_):
            received.set()
            await asyncio.Event().wait()

        async def welcomed(*_):
            notice.set()

        ws = WebSocket()
        with patch.multiple(self.bot, _safe_handle_message=blocked,
                _safe_handle_group_increase=welcomed, has_pending_retry=lambda: False,
                set_napcat_connected=Mock()):
            router = asyncio.create_task(self.bot.handler(ws))
            incoming.put_nowait(message())
            await asyncio.wait_for(received.wait(), 1)
            incoming.put_nowait({"post_type": "notice", "notice_type": "group_increase"})
            await asyncio.wait_for(notice.wait(), 1)
            incoming.put_nowait(None)
            await router
            self.assertFalse(self.bot._message_tasks)


if __name__ == "__main__":
    unittest.main()
