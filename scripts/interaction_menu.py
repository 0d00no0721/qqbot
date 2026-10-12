# -*- coding: utf-8 -*-
"""事件驱动的群互动菜单：回执关联、配置缓存、戳一戳与表情回应。

只在收到相关事件时工作；不轮询历史消息/回应列表，不启动定时清理任务。
所有状态属于当前进程，断线清理，重启后需重新打开菜单。
"""

import asyncio
import json
import math
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass


CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "config", "interaction_menu.json")
MAX_ITEMS = 6
MAX_RATE_RECORDS = 4096
DEDUP_SECONDS = 3.0


def _number_id(value, positive=True):
    """OneBot 的 ID 可能为整数或字符串；message_id 允许负数。"""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return ""
    text = str(value).strip()
    if not text.lstrip("-").isdigit():
        return ""
    try:
        number = int(text)
    except ValueError:
        return ""
    return str(number) if not positive or number > 0 else ""


@dataclass(frozen=True)
class MenuItem:
    emoji_id: str
    emoji: str
    label: str
    command: str


@dataclass(frozen=True)
class MenuSettings:
    items: tuple
    menu_ttl_seconds: float
    menu_cooldown_seconds: float
    action_cooldown_seconds: float


class MenuConfig:
    """按修改时间缓存；新配置无效时禁用互动，普通命令不受影响。"""

    def __init__(self, path=CONFIG_FILE, logger=None):
        self.path = path
        self.logger = logger
        self._stamp = None
        self._default = None
        self._groups = {}
        self.max_menus_per_group = 5
        self.max_menus_total = 500
        self.max_concurrent_actions = 8
        self.api_timeout_seconds = 10.0

    @staticmethod
    def _number(data, key, default, minimum=0, integer=False):
        value = data.get(key, default)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < minimum
                or (integer and not isinstance(value, int))):
            raise ValueError(f"{key} 必须是 >= {minimum} 的{'整数' if integer else '有限数值'}")
        return value

    @classmethod
    def _settings(cls, data):
        if not isinstance(data, dict):
            raise ValueError("菜单配置必须是对象")
        raw_items = data.get("items")
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= MAX_ITEMS:
            raise ValueError(f"items 必须包含 1 至 {MAX_ITEMS} 项")
        items, ids = [], set()
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("菜单项必须是对象")
            emoji_id = _number_id(item.get("emoji_id"))
            if not emoji_id or emoji_id in ids:
                raise ValueError("emoji_id 必须是互不重复的正整数或数字字符串")
            values = []
            for key in ("emoji", "label", "command"):
                value = item.get(key)
                if (not isinstance(value, str) or not value.strip()
                        or "\n" in value or "\r" in value or len(value) > 200):
                    raise ValueError(f"{key} 必须是 1 至 200 字符的单行文本")
                values.append(value.strip())
            items.append(MenuItem(emoji_id, *values))
            ids.add(emoji_id)
        return MenuSettings(
            tuple(items), cls._number(data, "menu_ttl_seconds", 600, 1),
            cls._number(data, "menu_cooldown_seconds", 30),
            cls._number(data, "action_cooldown_seconds", 3),
        )

    def get(self, group_id):
        try:
            stat = os.stat(self.path)
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = ("missing",)
        if stamp != self._stamp:
            self._stamp = stamp
            self._default, self._groups = None, {}
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError("配置根节点必须是对象")
                defaults = data.get("defaults", {})
                default = self._settings(defaults)
                groups = data.get("groups", {})
                if not isinstance(groups, dict):
                    raise ValueError("groups 必须是对象")
                parsed_groups = {}
                for gid, override in groups.items():
                    normalized = _number_id(gid)
                    if not normalized or not isinstance(override, dict):
                        raise ValueError("群号或群覆盖配置无效")
                    parsed_groups[normalized] = self._settings({**defaults, **override})
                limits = {
                    key: self._number(data, key, default_value, 1, integer=True)
                    for key, default_value in (
                        ("max_menus_per_group", 5), ("max_menus_total", 500),
                        ("max_concurrent_actions", 8))
                }
                timeout = self._number(data, "api_timeout_seconds", 10, 0.01)
                self._default, self._groups = default, parsed_groups
                for key, value in limits.items():
                    setattr(self, key, value)
                self.api_timeout_seconds = timeout
            except (OSError, ValueError, TypeError) as exc:
                if self.logger:
                    self.logger.warning(f"[互动菜单] 配置无效，暂停交互: {exc}")
        return self._groups.get(str(group_id), self._default)


class OneBotRPC:
    """仅为需要回执的交互请求添加 echo，不改变原有发送函数。"""

    def __init__(self):
        self.pending = {}

    async def call(self, websocket, action, params, timeout=10):
        echo = "interaction:" + uuid.uuid4().hex
        key = (websocket, echo)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future

        async def send_and_wait():
            await websocket.send(json.dumps({
                "action": action, "params": params, "echo": echo,
            }, ensure_ascii=False))
            return await future

        try:
            response = await asyncio.wait_for(send_and_wait(), timeout)
            if response.get("status") != "ok" or response.get("retcode") != 0:
                raise RuntimeError(f"{action} 失败，retcode={response.get('retcode')}")
            return response.get("data")
        finally:
            self.pending.pop(key, None)
            if not future.done():
                future.cancel()

    def resolve(self, websocket, data):
        if not isinstance(data, dict) or "post_type" in data:
            return False
        echo = data.get("echo")
        if not isinstance(echo, str) or not echo.startswith("interaction:"):
            return False
        future = self.pending.get((websocket, echo))
        if future is not None and not future.done():
            future.set_result(data)
        return True  # 迟到/重复回执也不进入消息路由

    def disconnect(self, websocket):
        for key in list(self.pending):
            if key[0] is websocket:
                future = self.pending.pop(key)
                if not future.done():
                    future.cancel()


@dataclass(frozen=True)
class ActiveMenu:
    self_id: str
    settings: MenuSettings
    expires_at: float


class InteractionMenus:
    """同步检查/占用名额后才创建任务，繁忙时不排队。"""

    def __init__(self, get_features, handle_command, logger=None,
                 config_path=CONFIG_FILE, clock=time.monotonic):
        self.get_features = get_features
        self.handle_command = handle_command
        self.logger = logger
        self.clock = clock
        self.config = MenuConfig(config_path, logger)
        self.rpc = OneBotRPC()
        self.menus = OrderedDict()
        self._tasks = {}
        self._opening = set()
        self._users_busy = set()
        self._menu_cooldowns = OrderedDict()
        self._action_cooldowns = OrderedDict()
        self._seen = OrderedDict()

    def enabled(self, group_id):
        feat = self.get_features(str(group_id))
        return bool(feat.get("commands") and
                    (feat.get("poke_menu") or feat.get("reaction_menu")))

    def _log(self, message):
        if self.logger:
            self.logger.warning(f"[互动菜单] {message}")

    def _prune(self, now):
        for key, menu in list(self.menus.items()):
            if menu.expires_at <= now:
                del self.menus[key]
        for cache in (self._menu_cooldowns, self._action_cooldowns, self._seen):
            for key, expires in list(cache.items()):
                if expires <= now:
                    del cache[key]

    @staticmethod
    def _remember(cache, key, expires):
        cache[key] = expires
        cache.move_to_end(key)
        while len(cache) > MAX_RATE_RECORDS:
            cache.popitem(last=False)

    def _spawn(self, websocket, job, release):
        async def run():
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log(f"处理失败: {type(exc).__name__}: {exc}")

        def finished(task):
            # 任务尚未开始就被取消时，协程内的 finally 不会执行。
            release()
            self._tasks.pop(task, None)

        task = asyncio.create_task(run())
        self._tasks[task] = websocket
        task.add_done_callback(finished)
        return task

    def start_menu(self, websocket, data, poked=False):
        gid, uid, self_id = (_number_id(data.get(key))
                             for key in ("group_id", "user_id", "self_id"))
        if not gid or not uid or not self_id or uid == self_id:
            return None
        feat = self.get_features(gid)
        if (not feat.get("commands") or
                not (feat.get("poke_menu") if poked else
                     (feat.get("poke_menu") or feat.get("reaction_menu")))):
            return None
        if poked and _number_id(data.get("target_id")) != self_id:
            return None
        settings = self.config.get(gid)
        if settings is None:
            return None
        now = self.clock()
        self._prune(now)
        key = (websocket, gid)
        if (key in self._opening or self._menu_cooldowns.get(key, 0) > now
                or len(self._tasks) >= self.config.max_concurrent_actions):
            return None
        self._opening.add(key)
        self._remember(self._menu_cooldowns, key, now + settings.menu_cooldown_seconds)
        return self._spawn(
            websocket, lambda: self._send_menu(websocket, gid, self_id, settings),
            lambda: self._opening.discard(key),
        )

    async def _send_menu(self, websocket, gid, self_id, settings):
        feat = self.get_features(gid)
        if not feat.get("commands") or not (feat.get("poke_menu") or feat.get("reaction_menu")):
            return
        lines = ["【互动菜单】全群可用，操作记在点击者本人名下。"]
        for item in settings.items:
            lines.append(f"{item.emoji} {item.label}  ｜  @bot {item.command}")
        reactions = bool(self.get_features(gid).get("reaction_menu"))
        if reactions:
            lines.append(f"点击下方对应表情；有效期 {settings.menu_ttl_seconds:g} 秒。")
            lines.append("取消不执行；取消后重新添加可再次操作。过期请戳我或 @bot 互动菜单。")
        lines.append("没有表情入口时，请手动 @机器人 发送上面的命令。")
        result = await self.rpc.call(websocket, "send_group_msg", {
            "group_id": int(gid),
            "message": [{"type": "text", "data": {"text": "\n".join(lines)}}],
        }, self.config.api_timeout_seconds)
        if not reactions:
            return
        mid = _number_id(result.get("message_id") if isinstance(result, dict) else None, False)
        if not mid:
            self._log("菜单回执缺少 message_id，保留文字入口")
            return
        now = self.clock()
        self._prune(now)
        key = (websocket, gid, mid)
        self.menus[key] = ActiveMenu(self_id, settings, now + settings.menu_ttl_seconds)
        group_keys = [k for k in self.menus if k[:2] == (websocket, gid)]
        for old_key in group_keys[:-self.config.max_menus_per_group]:
            del self.menus[old_key]
        while len(self.menus) > self.config.max_menus_total:
            self.menus.popitem(last=False)
        for item in settings.items:
            feat = self.get_features(gid)
            if not feat.get("commands") or not feat.get("reaction_menu"):
                break
            try:
                result = await self.rpc.call(websocket, "set_msg_emoji_like", {
                    "message_id": int(mid), "emoji_id": item.emoji_id, "set": True,
                }, self.config.api_timeout_seconds)
                if isinstance(result, dict):
                    code = result.get("result")
                    if code is False or (type(code) is int and code != 0):
                        raise RuntimeError(f"设置回应失败，result={code}")
            except (RuntimeError, OSError, asyncio.TimeoutError) as exc:
                self._log(f"表情 {item.emoji_id} 设置失败，保留文字入口: {exc}")

    def handle_notice(self, websocket, data):
        if data.get("notice_type") == "notify" and data.get("sub_type") == "poke":
            return self.start_menu(websocket, data, poked=True)
        if data.get("notice_type") != "group_msg_emoji_like" or data.get("is_add") is not True:
            return None
        gid, uid, self_id = (_number_id(data.get(key))
                             for key in ("group_id", "user_id", "self_id"))
        mid = _number_id(data.get("message_id"), False)
        if not gid or not uid or not self_id or not mid or uid == self_id:
            return None
        menu = self.menus.get((websocket, gid, mid))
        now = self.clock()
        if menu is None or menu.self_id != self_id or menu.expires_at <= now:
            if menu is not None and menu.expires_at <= now:
                self.menus.pop((websocket, gid, mid), None)
            return None
        feat = self.get_features(gid)
        if not feat.get("commands") or not feat.get("reaction_menu"):
            return None
        likes = data.get("likes")
        if not isinstance(likes, list):
            return None
        ids = {_number_id(like.get("emoji_id")) for like in likes if isinstance(like, dict)}
        item = next((item for item in menu.settings.items if item.emoji_id in ids), None)
        if item is None or self.config.get(gid) is None:
            return None
        self._prune(now)
        user_key = (websocket, gid, uid)
        # time 相同的重放在菜单有效期内只处理一次；缺少 time 时用短窗口。
        event_time = _number_id(data.get("time"))
        event_key = (websocket, gid, mid, uid, item.emoji_id, event_time)
        if (user_key in self._users_busy or self._action_cooldowns.get(user_key, 0) > now
                or self._seen.get(event_key, 0) > now
                or len(self._tasks) >= self.config.max_concurrent_actions):
            return None
        self._users_busy.add(user_key)
        self._remember(self._action_cooldowns, user_key,
                       now + max(DEDUP_SECONDS, menu.settings.action_cooldown_seconds))
        self._remember(self._seen, event_key,
                       menu.expires_at if event_time else now + DEDUP_SECONDS)
        # 不借用菜单发起者/机器人的身份，也不伪造管理员角色。
        command_data = {
            "self_id": int(self_id), "group_id": int(gid), "user_id": int(uid),
            "sender": {"user_id": int(uid), "nickname": "用户"},
            "message": [{"type": "text", "data": {"text": item.command}}],
            "raw_message": item.command,
        }

        async def dispatch():
            # 任务开始前再检查开关，处理排入本轮事件循环后管理员关闭的情况。
            current = self.get_features(gid)
            if current.get("commands") and current.get("reaction_menu"):
                handled = await self.handle_command(websocket, command_data, item.command, item.command)
                if not handled:
                    self._log(f"配置的命令未匹配: {item.command}")

        return self._spawn(websocket, dispatch, lambda: self._users_busy.discard(user_key))

    def disconnect(self, websocket):
        self.rpc.disconnect(websocket)
        for task, connection in list(self._tasks.items()):
            if connection is websocket:
                task.cancel()
        for cache in (self.menus, self._menu_cooldowns, self._action_cooldowns, self._seen):
            for key in list(cache):
                if key[0] is websocket:
                    del cache[key]
        self._opening = {key for key in self._opening if key[0] is not websocket}
        self._users_busy = {key for key in self._users_busy if key[0] is not websocket}
