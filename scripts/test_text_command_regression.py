# -*- coding: utf-8 -*-
"""公共文字入口回归：使用临时业务文件，不接触真实群资料。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from scripts.command_handler import handle_command
from scripts.commands import _state
from scripts import sign_in


class TextCommandTests(unittest.IsolatedAsyncioTestCase):
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
                self.assertFalse(await handle_command(None, data, "离线测试的普通聊天", "离线测试的普通聊天"))

    async def test_admin_command_retains_permission_check(self):
        engine = Mock()
        engine.get_active_character.return_value = {"admin_ids": ["1"], "character": "offline"}
        replies = AsyncMock(return_value=True)
        with patch.multiple(_state, decision_engine=engine, send_message_fn=replies):
            data = {"group_id": 100, "user_id": 2, "sender": {}}
            await handle_command(None, data, "停用角色", "停用角色")
            engine.set_active_character.assert_not_called()
            self.assertIn("只有管理员", replies.call_args.args[2])
            data["user_id"] = 1
            await handle_command(None, data, "停用角色", "停用角色")
            engine.set_active_character.assert_called_once_with(False, "offline")


if __name__ == "__main__":
    unittest.main()
