# -*- coding: utf-8 -*-
"""互动菜单离线回放：python -m unittest scripts.test_interaction_menu -v。

无真实 QQ/模型请求；配置和签到记录均使用临时目录。
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts.interaction_menu import CONFIG_FILE, InteractionMenus, MenuConfig, OneBotRPC


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeWebSocket:
    remote_address = ("offline-test", 0)

    def __init__(self, rpc, auto_reply=True):
        self.rpc = rpc
        self.auto_reply = auto_reply
        self.sent = []
        self.incoming = asyncio.Queue()
        self.use_router = False
        self.fail_action = None
        self.next_mid = -100
        self.sent_event = asyncio.Event()

    async def send(self, raw):
        payload = json.loads(raw)
        self.sent.append(payload)
        self.sent_event.set()
        if not self.auto_reply or "echo" not in payload:
            return
        result = {"result": True}
        if payload["action"] == "send_group_msg":
            self.next_mid -= 1
            result = {"message_id": self.next_mid}
        response = {"status": "ok", "retcode": 0, "data": result, "echo": payload["echo"]}
        if payload["action"] == self.fail_action:
            response.update(status="failed", retcode=1404, data=None)
        asyncio.get_running_loop().call_soon(self.reply, response)

    def reply(self, response):
        if self.use_router:
            self.incoming.put_nowait(json.dumps(response))
        else:
            self.rpc.resolve(self, response)

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value


def poke(**updates):
    return {"post_type": "notice", "notice_type": "notify", "sub_type": "poke",
            "self_id": 90001, "group_id": 10001, "user_id": 20001,
            "target_id": 90001, **updates}


def reaction(mid, **updates):
    return {"post_type": "notice", "notice_type": "group_msg_emoji_like",
            "self_id": 90001, "group_id": 10001, "user_id": 20001,
            "message_id": mid, "is_add": True,
            "likes": [{"emoji_id": "128077", "count": 2}], **updates}


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "menus.json"
        self.config_data = json.loads(Path(CONFIG_FILE).read_text(encoding="utf-8"))
        self.stamp = 1_800_000_000_000_000_000
        self.write_config()
        self.clock = Clock()
        self.features = {"10001": {"commands": True, "poke_menu": True, "reaction_menu": True}}
        self.dispatch = AsyncMock(return_value=True)
        self.logger = Mock()
        self.menus = InteractionMenus(lambda gid: self.features.get(gid, {}),
                                      self.dispatch, self.logger, self.path, self.clock)
        self.ws = FakeWebSocket(self.menus.rpc)

    def write_config(self):
        self.path.write_text(json.dumps(self.config_data, ensure_ascii=False), encoding="utf-8")
        self.stamp += 1_000_000
        os.utime(self.path, ns=(self.stamp, self.stamp))

    async def asyncTearDown(self):
        tasks = list(self.menus._tasks)
        for connection in set(self.menus._tasks.values()) | {self.ws}:
            self.menus.disconnect(connection)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.temp.cleanup()

    async def open_menu(self, **updates):
        task = self.menus.handle_notice(self.ws, poke(**updates))
        self.assertIsNotNone(task)
        await task
        return self.ws.next_mid

    async def click(self, mid, **updates):
        task = self.menus.handle_notice(self.ws, reaction(mid, **updates))
        if task is not None:
            await task
        return task

    async def test_menu_api_budget_and_display(self):
        mid = await self.open_menu()
        self.assertEqual([p["action"] for p in self.ws.sent],
                         ["send_group_msg"] + ["set_msg_emoji_like"] * 3)
        text = self.ws.sent[0]["params"]["message"][0]["data"]["text"]
        for expected in ("👍 签到", "🍜 今天吃啥", "❔ 帮助", "600 秒", "@bot 签到"):
            self.assertIn(expected, text)
        self.assertIn((self.ws, "10001", str(mid)), self.menus.menus)
        self.assertFalse(self.menus.rpc.pending)
        await self.click(mid, user_id=90001)
        self.dispatch.assert_not_called()

    async def test_poke_gates_and_manual_entry(self):
        for changes in ({"target_id": 20002}, {"user_id": 90001}, {"group_id": None},
                        {"group_id": 99999}, {"user_id": 0}, {"self_id": None}):
            self.assertIsNone(self.menus.handle_notice(self.ws, poke(**changes)))
        self.features["10001"]["poke_menu"] = False
        self.assertIsNone(self.menus.handle_notice(self.ws, poke()))
        task = self.menus.start_menu(self.ws, poke())
        self.assertIsNotNone(task)
        await task
        self.clock.advance(31)
        self.features["10001"]["commands"] = False
        self.assertIsNone(self.menus.start_menu(self.ws, poke()))

    async def test_poke_only_sends_plain_menu(self):
        self.features["10001"]["reaction_menu"] = False
        mid = await self.open_menu()
        self.assertEqual(len(self.ws.sent), 1)
        self.assertFalse(self.menus.menus)
        await self.click(mid)
        self.dispatch.assert_not_called()

    async def test_shared_menu_uses_actual_actor_and_no_extra_api(self):
        mid = await self.open_menu()
        first = self.menus.handle_notice(self.ws, reaction(mid))
        second = self.menus.handle_notice(self.ws, reaction(mid, user_id="20002"))
        await asyncio.gather(first, second)
        self.assertEqual([call.args[1]["user_id"] for call in self.dispatch.call_args_list],
                         [20001, 20002])
        self.assertEqual(len(self.ws.sent), 4)
        for call in self.dispatch.call_args_list:
            self.assertEqual(call.args[2:], ("签到", "签到"))
            self.assertNotIn("role", call.args[1]["sender"])

    async def test_replay_cancel_readd_and_cooldown(self):
        mid = await self.open_menu()
        await self.click(mid, time=100)
        self.assertIsNone(await self.click(mid, time=100))
        self.assertIsNone(await self.click(mid, is_add=False, time=101))
        self.assertIsNone(await self.click(mid, time=102))
        self.clock.advance(4)
        self.assertIsNone(await self.click(mid, time=100))  # 迟到的相同事件
        self.assertIsNotNone(await self.click(mid, time=104))
        self.assertEqual(self.dispatch.await_count, 2)

    async def test_missing_timestamp_can_repeat_after_cooldown(self):
        mid = await self.open_menu()
        await self.click(mid)
        self.clock.advance(4)
        await self.click(mid)
        self.assertEqual(self.dispatch.await_count, 2)

    async def test_invalid_and_unrelated_reactions_are_ignored(self):
        mid = await self.open_menu()
        for changes in ({"message_id": -999}, {"group_id": 10002}, {"user_id": None},
                        {"user_id": False}, {"self_id": 99999}, {"is_add": "true"},
                        {"likes": None}, {"likes": [None, {"emoji_id": "999999"}]}):
            self.assertIsNone(await self.click(mid, **changes))
        self.features["10001"]["reaction_menu"] = False
        self.assertIsNone(await self.click(mid))
        self.dispatch.assert_not_called()

    async def test_snapshot_survives_valid_config_change(self):
        mid = await self.open_menu()
        self.config_data["groups"]["10001"] = {
            "items": [{"emoji_id": "128077", "emoji": "👍", "label": "说明", "command": "帮助"}],
            "menu_cooldown_seconds": 1,
        }
        self.write_config()
        await self.click(mid)
        self.assertEqual(self.dispatch.call_args.args[2], "签到")
        self.clock.advance(31)
        new_mid = await self.open_menu()
        await self.click(new_mid)
        self.assertEqual(self.dispatch.call_args.args[2], "帮助")
        settings = self.menus.config.get("10001")
        self.assertEqual(settings.menu_ttl_seconds, 600)
        self.assertEqual(len(settings.items), 1)
        self.assertEqual(len(self.menus.config.get("10002").items), 3)

    async def test_expiration_and_menu_capacity(self):
        self.config_data.update(max_menus_per_group=2, max_menus_total=2)
        self.write_config()
        first = await self.open_menu()
        self.clock.advance(31)
        second = await self.open_menu()
        self.clock.advance(31)
        third = await self.open_menu()
        self.assertIsNone(await self.click(first))
        self.assertEqual(len(self.menus.menus), 2)
        self.clock.advance(600)
        self.assertIsNone(await self.click(second))
        self.assertIsNone(await self.click(third))
        self.assertFalse(self.menus.menus)

    async def test_global_capacity_evicts_oldest_group(self):
        self.config_data["max_menus_total"] = 1
        self.write_config()
        self.features["10002"] = dict(self.features["10001"])
        first = await self.open_menu()
        await self.open_menu(group_id=10002)
        self.assertEqual(len(self.menus.menus), 1)
        self.assertIsNone(await self.click(first))

    async def test_concurrent_limits_do_not_queue(self):
        self.config_data["max_concurrent_actions"] = 1
        self.write_config()
        mid = await self.open_menu()
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(*_):
            entered.set()
            await release.wait()
            return True

        self.dispatch.side_effect = slow
        first = self.menus.handle_notice(self.ws, reaction(mid))
        await entered.wait()
        self.clock.advance(4)
        self.assertIsNone(self.menus.handle_notice(self.ws, reaction(mid)))
        self.assertIsNone(self.menus.handle_notice(self.ws, reaction(mid, user_id=20002)))
        self.assertEqual(len(self.menus._tasks), 1)
        release.set()
        await first
        await self.click(mid, user_id=20002)
        self.assertEqual(self.dispatch.await_count, 2)

    async def test_send_failure_has_no_menu_or_retry(self):
        self.ws.fail_action = "send_group_msg"
        await self.open_menu()
        self.assertFalse(self.menus.menus)
        self.assertEqual(len(self.ws.sent), 1)
        self.assertTrue(self.logger.warning.called)
        self.assertFalse(self.menus.rpc.pending)

    async def test_reaction_failure_keeps_text_and_manual_reaction(self):
        self.ws.fail_action = "set_msg_emoji_like"
        mid = await self.open_menu()
        self.assertEqual(len(self.ws.sent), 4)
        await self.click(mid)
        self.dispatch.assert_awaited_once()
        self.assertEqual(self.logger.warning.call_count, 3)

    async def test_disconnect_clears_inflight_and_ignores_late_reply(self):
        self.ws.auto_reply = False
        task = self.menus.handle_notice(self.ws, poke())
        await self.ws.sent_event.wait()
        payload = self.ws.sent[0]
        self.menus.disconnect(self.ws)
        await asyncio.gather(task, return_exceptions=True)
        self.ws.reply({"status": "ok", "retcode": 0, "data": {"message_id": -1},
                       "echo": payload["echo"]})
        self.assertFalse(self.menus.rpc.pending)
        self.assertFalse(self.menus.menus)
        self.assertFalse(self.menus._opening)
        self.assertFalse(self.menus._menu_cooldowns)
        self.ws.auto_reply = True
        await self.open_menu()

    async def test_invalid_config_logs_once_and_recovers(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.menus.handle_notice(self.ws, poke()))
        self.assertIsNone(self.menus.handle_notice(self.ws, poke()))
        self.logger.warning.assert_called_once()
        self.assertFalse(self.ws.sent)
        self.write_config()
        await self.open_menu()

    async def test_cancel_before_task_starts_releases_slot(self):
        self.config_data["defaults"]["menu_cooldown_seconds"] = 0
        self.write_config()
        task = self.menus.handle_notice(self.ws, poke())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertFalse(self.menus._opening)
        self.assertFalse(self.menus._tasks)
        self.assertFalse(self.ws.sent)
        await self.open_menu()

    async def test_invalid_menu_schema(self):
        cases = [
            {"items": []},
            {"items": self.config_data["defaults"]["items"] * 2},
            {"menu_ttl_seconds": float("inf")},
            {"action_cooldown_seconds": -1},
            {"items": [{"emoji_id": 1, "emoji": "?", "label": "?", "command": "\n"}]},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    MenuConfig._settings({**self.config_data["defaults"], **changes})

    async def test_idle_has_no_new_tasks_network_or_periodic_cleanup(self):
        before = set(asyncio.all_tasks())
        mid = await self.open_menu()
        await asyncio.sleep(0)  # 仅让任务结束回调运行，不等待墙钟周期
        sent = len(self.ws.sent)
        self.clock.advance(3600)
        await asyncio.sleep(0)
        self.assertEqual(len(self.ws.sent), sent)
        self.assertFalse(self.menus._tasks)
        self.assertEqual(set(asyncio.all_tasks()), before)
        self.assertIn((self.ws, "10001", str(mid)), self.menus.menus)  # 清理按需进行
        self.assertIsNone(await self.click(mid))


    async def test_real_sign_in_and_existing_text_commands(self):
        from scripts.command_handler import handle_command
        from scripts.commands import _state
        from scripts import sign_in

        self.menus.handle_command = handle_command
        replies = AsyncMock(return_value=True)
        sign_path = Path(self.temp.name) / "sign_in.json"
        food_path = Path(self.temp.name) / "food.json"
        food_path.write_text(json.dumps({"10001": {"20002": ["米饭"]}}), encoding="utf-8")
        with patch.object(sign_in, "SIGN_IN_DATA_FILE", str(sign_path)), patch.multiple(
                _state, send_message_fn=replies, _sign_in_lock=asyncio.Lock(), DATA_FILE=str(food_path)):
            mid = await self.open_menu()
            await self.click(mid)
            await self.click(mid, user_id=20002)
            # 原有文字入口与点击入口落到同一个业务实现。
            await handle_command(self.ws, poke(user_id=20003), "签到", "签到")
            self.assertEqual(set(json.loads(sign_path.read_text(encoding="utf-8"))["10001"]),
                             {"20001", "20002", "20003"})
            self.assertEqual([c.kwargs["at_user"] for c in replies.call_args_list],
                             ["20001", "20002", "20003"])
            await handle_command(self.ws, poke(), "今天吃啥", "今天吃啥")
            self.assertIn("米饭", replies.call_args.args[2])
            await handle_command(self.ws, poke(), "帮助", "帮助")
            self.assertIn("互动菜单", replies.call_args.args[2])

    async def test_real_admin_command_checks_clicker_not_menu_owner(self):
        from scripts.command_handler import handle_command
        from scripts.commands import _state

        self.config_data["defaults"]["items"] = [
            {"emoji_id": "128077", "emoji": "👍", "label": "停用角色", "command": "停用角色"}]
        self.write_config()
        self.menus.handle_command = handle_command
        engine = Mock()
        engine.get_active_character.return_value = {"admin_ids": ["20001"], "character": "test"}
        replies = AsyncMock(return_value=True)
        with patch.multiple(_state, decision_engine=engine, send_message_fn=replies):
            mid = await self.open_menu()  # 发起者 20001 是管理员
            await self.click(mid, user_id=20002, sender={"role": "owner", "user_id": 20001})
            engine.set_active_character.assert_not_called()
            self.assertIn("只有管理员", replies.call_args.args[2])
            await self.click(mid, user_id=20001)
            engine.set_active_character.assert_called_once_with(False, "test")

    async def test_at_command_uses_registered_menu_handler(self):
        import reverse_bot as bot
        from scripts.commands import _state

        data = {**poke(), "post_type": "message", "message_type": "group", "message_id": 777,
                "raw_message": "[CQ:at,qq=90001] 互动菜单"}
        with patch.multiple(bot, BOT_QQ="90001", _get_group_features=lambda gid: self.features.get(gid, {})), \
                patch.object(_state, "interaction_menus", self.menus), \
                patch.object(_state, "processed_msg_ids", set()):
            await bot.handle_message(self.ws, data)
        self.assertEqual(len(self.ws.sent), 4)

    async def test_ws_receives_messages_while_waiting_for_receipt(self):
        import reverse_bot as bot

        received, welcomed = asyncio.Event(), asyncio.Event()

        async def normal_message(*_):
            received.set()

        async def new_member(*_):
            welcomed.set()

        self.ws.auto_reply = False
        self.ws.use_router = True
        with patch.multiple(bot, interaction_menus=self.menus, bot_logger=Mock(),
                            has_pending_retry=lambda: False, set_napcat_connected=Mock(),
                            _safe_handle_message=normal_message, _safe_handle_group_increase=new_member):
            router = asyncio.create_task(bot.handler(self.ws))
            try:
                self.ws.incoming.put_nowait(json.dumps(poke()))
                await asyncio.wait_for(self.ws.sent_event.wait(), 1)
                self.ws.incoming.put_nowait("[]")
                self.ws.incoming.put_nowait(json.dumps({"post_type": "message", "message_type": "group"}))
                self.ws.incoming.put_nowait(json.dumps({"post_type": "notice", "notice_type": "group_increase"}))
                await asyncio.wait_for(received.wait(), 1)
                await asyncio.wait_for(welcomed.wait(), 1)
                self.assertTrue(self.menus.rpc.pending)
                self.ws.auto_reply = True
                self.ws.reply({"status": "ok", "retcode": 0, "data": {"message_id": -10},
                               "echo": self.ws.sent[0]["echo"]})
                await asyncio.wait_for(asyncio.gather(*list(self.menus._tasks)), 1)
                self.assertEqual(len(self.ws.sent), 4)
            finally:
                self.ws.incoming.put_nowait(None)
                await router
        self.assertFalse(self.menus.menus)
        self.assertFalse(self.menus.rpc.pending)


class RPCTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_includes_send_and_does_not_retry(self):
        rpc = OneBotRPC()
        ws = FakeWebSocket(rpc, auto_reply=False)
        with self.assertRaises(asyncio.TimeoutError):
            await rpc.call(ws, "send_group_msg", {}, timeout=0.01)
        self.assertEqual(len(ws.sent), 1)
        self.assertFalse(rpc.pending)
        slow_ws = Mock()
        async def blocked_send(_):
            await asyncio.Event().wait()

        slow_ws.send = blocked_send
        with self.assertRaises(asyncio.TimeoutError):
            await rpc.call(slow_ws, "send_group_msg", {}, timeout=0.01)
        self.assertFalse(rpc.pending)

    async def test_echo_is_scoped_to_connection(self):
        rpc = OneBotRPC()
        ws = FakeWebSocket(rpc, auto_reply=False)
        other = FakeWebSocket(rpc)
        call = asyncio.create_task(rpc.call(ws, "send_group_msg", {}))
        await ws.sent_event.wait()
        response = {"status": "ok", "retcode": 0, "data": {"message_id": -1},
                    "echo": ws.sent[0]["echo"]}
        rpc.resolve(other, response)
        self.assertFalse(call.done())
        rpc.resolve(ws, response)
        self.assertEqual(await call, {"message_id": -1})
        self.assertTrue(rpc.resolve(ws, response))
        self.assertFalse(rpc.pending)


class GroupConfigTests(unittest.TestCase):
    def test_old_group_fields_preserved_and_new_switches_off(self):
        from scripts import group_manager
        entry = {"group_name": "测试", "extra": "保留", "features": {"commands": True, "vision": True}}
        with patch.object(group_manager, "load_defaults", return_value={}):
            updated = group_manager._normalize_entry(entry)
        self.assertTrue(updated["features"]["vision"])
        self.assertTrue(updated["features"]["commands"])
        self.assertFalse(updated["features"]["poke_menu"])
        self.assertFalse(updated["features"]["reaction_menu"])
        self.assertEqual(updated["extra"], "保留")
        self.assertNotIn("poke_menu", entry["features"])
        with patch.object(group_manager, "load_defaults", return_value={
                "features": {"poke_menu": True, "reaction_menu": True}}):
            self.assertTrue(group_manager.get_default_features()["poke_menu"])
            self.assertFalse(group_manager._normalize_entry(entry)["features"]["poke_menu"])


if __name__ == "__main__":
    unittest.main()
