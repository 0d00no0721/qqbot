# -*- coding: utf-8 -*-
"""主程序接线和原有命令回归；业务数据全部重定向到临时目录。"""

import asyncio
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts.command_handler import handle_command
from scripts.commands import _state
from scripts import sign_in, voice_output


class VoiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_dispatch_does_not_invoke_model(self):
        service = Mock(read=AsyncMock())
        replies = AsyncMock(return_value=True)
        with patch("scripts.commands.tts.get_service", return_value=service), patch.object(_state, "send_message_fn", replies):
            data = {"group_id": 100, "user_id": 1, "message_id": 7, "sender": {}}
            self.assertTrue(await handle_command(None, data, "朗读 你好呀", ""))
            self.assertEqual(service.read.call_args.args[1:5], (100, 1, 7, "你好呀"))
            self.assertTrue(await handle_command(None, data, "朗读", ""))
            self.assertIn("用法", replies.call_args.args[2])
            self.assertEqual(service.read.await_count, 1)

    async def test_sign_in_food_and_help_use_original_handlers(self):
        with tempfile.TemporaryDirectory() as temp:
            sign_path = Path(temp) / "sign.json"
            food_path = Path(temp) / "food.json"
            food_path.write_text(json.dumps({"100": {"2": ["米饭"]}}), encoding="utf-8")
            replies = AsyncMock(return_value=True)
            with patch.object(sign_in, "SIGN_IN_DATA_FILE", str(sign_path)), patch.multiple(
                    _state, send_message_fn=replies, DATA_FILE=str(food_path), _sign_in_lock=asyncio.Lock()):
                for uid in (1, 2):
                    data = {"group_id": 100, "user_id": uid, "sender": {"nickname": "离线"}}
                    self.assertTrue(await handle_command(None, data, "签到", "签到"))
                self.assertEqual(set(json.loads(sign_path.read_text(encoding="utf-8"))["100"]), {"1", "2"})
                self.assertTrue(await handle_command(None, data, "今天吃啥", "今天吃啥"))
                self.assertIn("米饭", replies.call_args.args[2])
                self.assertTrue(await handle_command(None, data, "帮助", "帮助"))
                self.assertIn("可用命令", replies.call_args.args[2])
                self.assertTrue(await handle_command(None, data, "小巧思帮助", ""))
                self.assertIn("朗读", replies.call_args.args[2])
                self.assertFalse(await handle_command(None, data, "离线普通聊天", ""))

    async def test_admin_permission_unchanged(self):
        engine = Mock()
        engine.get_active_character.return_value = {"admin_ids": ["1"], "character": "offline"}
        replies = AsyncMock(return_value=True)
        with patch.multiple(_state, decision_engine=engine, send_message_fn=replies):
            data = {"group_id": 100, "user_id": 2, "sender": {}}
            await handle_command(None, data, "停用角色", "")
            engine.set_active_character.assert_not_called()
            self.assertIn("只有管理员", replies.call_args.args[2])

    async def test_main_routes_only_final_chat_reply_and_cleans_connection(self):
        # 导入时屏蔽 DecisionEngine 初始化的文件写入；实际处理方法继续使用上游代码。
        with patch("scripts.decision_engine.DecisionEngine", return_value=Mock()):
            bot = importlib.import_module("reverse_bot")
        engine = Mock()
        engine.should_reply = AsyncMock(return_value=(True, "离线回答"))
        service = Mock(reply=AsyncMock())
        class Socket:
            remote_address = "offline"
            async def __aiter__(self):
                if False:
                    yield None
        socket = Socket()
        data = {"group_id": 100, "user_id": 2, "self_id": 1, "message_id": 123456,
                "raw_message": f"[CQ:at,qq={bot.BOT_QQ}] 你好", "sender": {"nickname": "离线"}}
        with patch.multiple(bot, decision_engine=engine, PROFILE_ENABLED=False,
                            _feature_on=lambda gid, key: key == "decision_engine"), \
                patch.object(_state, "processed_msg_ids", set()), \
                patch.object(voice_output, "get_service", return_value=service):
            await bot.handle_message(socket, data)
        service.reply.assert_awaited_once()
        self.assertEqual(service.reply.call_args.args[1:5], (100, "2", 123456, "离线回答"))
        with patch.object(voice_output, "disconnect", new_callable=AsyncMock) as disconnect, \
                patch.object(bot, "has_pending_retry", return_value=False), \
                patch.object(bot, "set_napcat_connected"):
            await bot.handler(socket)
            disconnect.assert_awaited_once_with(socket)

    async def test_group_command_switch_blocks_read_command(self):
        with patch("scripts.decision_engine.DecisionEngine", return_value=Mock()):
            bot = importlib.import_module("reverse_bot")
        data = {"group_id": 999, "user_id": 2, "self_id": 1, "message_id": 123457,
                "raw_message": f"[CQ:at,qq={bot.BOT_QQ}] 朗读 你好", "sender": {}}
        with patch.object(bot, "_feature_on", return_value=False), \
                patch.object(bot, "handle_command", new_callable=AsyncMock) as command, \
                patch.object(_state, "processed_msg_ids", set()):
            await bot.handle_message(None, data)
            command.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
