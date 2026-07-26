# -*- coding: utf-8 -*-
"""
指令注册表与分发器。

用法::

    from ._registry import register, CommandContext

    @register("Yau", match_type="prefix")
    async def cmd_yau(ctx: CommandContext) -> bool:
        ...
        return True   # 返回 True 表示已处理

匹配类型：
- ``exact``    : msg == pattern
- ``prefix``   : msg == pattern 或 msg.startswith(pattern + " ")（避免误触发）
- ``substring``: pattern in msg
- ``regex``    : re.match(pattern, msg)，handler 接收第二个参数 match
- ``custom``   : 调用 pattern(ctx.clean_message) 返回 bool / match 对象

同级按注册顺序遍历，首个匹配且 handler 返回 True 即停止。
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Union

from . import _state


@dataclass
class CommandContext:
    """单条 @bot 消息的处理上下文，handler 的唯一参数。"""
    websocket: object
    data: dict
    clean_message: str
    raw_message: str
    group_id: int = 0
    user_id: int = 0
    uid_str: str = ""
    nickname: str = ""

    def is_admin(self) -> bool:
        """检查当前用户是否为角色管理员。"""
        de = _state.decision_engine
        if not de:
            return False
        cfg = de.get_active_character()
        return self.uid_str in cfg.get("admin_ids", [])

    async def send(self, message: str, at_user: Optional[str] = None) -> bool:
        """发送群文本消息。"""
        fn = _state.send_message_fn
        if fn is None:
            return False
        return await fn(self.websocket, self.group_id, message, at_user=at_user)

    async def send_image(self, image_path: str) -> bool:
        """发送群图片。"""
        fn = _state.send_group_image_fn
        if fn is None:
            return False
        await fn(self.websocket, self.group_id, image_path)
        return True

    async def send_file(self, file_path: str) -> bool:
        """发送群文件。"""
        fn = _state.send_group_file_fn
        if fn is None:
            return False
        await fn(self.websocket, self.group_id, file_path)
        return True

    async def send_segments(self, segments: list) -> bool:
        """发送多段消息（at + image + text 等），直接走 websocket。

        segments 形如 ``[{"type":"at","data":{"qq":uid}}, ...]``。
        """
        import json
        payload = {
            "action": "send_group_msg",
            "params": {"group_id": self.group_id, "message": segments},
        }
        try:
            await self.websocket.send(json.dumps(payload))
            return True
        except Exception:
            if _state.bot_logger:
                _state.bot_logger.warning("  ⚠️  多段消息发送失败: 连接已断开，消息已丢弃")
            return False

    def log(self, msg: str) -> None:
        """记录日志（若 logger 已注入）。"""
        if _state.bot_logger:
            _state.bot_logger.info(msg)


@dataclass
class _Spec:
    name: str
    match_type: str
    pattern: object  # str | Callable
    handler: Callable
    priority: int = 0


_REGISTRY: List[_Spec] = []


def register(
    name: str,
    match_type: str = "exact",
    pattern: Optional[Union[str, Callable]] = None,
    priority: int = 0,
):
    """装饰器：注册一条指令。

    ``pattern`` 缺省时取 ``name``。
    """
    pat = pattern if pattern is not None else name

    def deco(fn: Callable) -> Callable:
        _REGISTRY.append(_Spec(name=name, match_type=match_type,
                               pattern=pat, handler=fn, priority=priority))
        return fn

    return deco


def _match(spec: _Spec, msg: str):
    """返回 (matched, extra)。matched 为 True 时 extra 传给 handler。"""
    mt = spec.match_type
    if mt == "exact":
        return (msg == spec.pattern, None)
    if mt == "prefix":
        p = spec.pattern
        return (msg == p or msg.startswith(p + " "), None)
    if mt == "substring":
        return (spec.pattern in msg, None)
    if mt == "regex":
        m = re.match(spec.pattern, msg)
        return (m is not None, m)
    if mt == "custom":
        r = spec.pattern(msg)
        if r is False or r is None:
            return (False, None)
        return (True, r)
    return (False, None)


async def dispatch(ctx: CommandContext) -> bool:
    """遍历注册表，首个匹配且 handler 返回 True 的指令即停止。

    返回 True 表示已处理，False 表示无指令匹配。
    同级（priority）按注册顺序遍历；priority 高的同级优先。
    """
    msg = ctx.clean_message
    # 按 priority 降序、同级保持注册顺序
    ordered = sorted(enumerate(_REGISTRY), key=lambda t: (-t[1].priority, t[0]))
    for _, spec in ordered:
        try:
            matched, extra = _match(spec, msg)
        except Exception:
            continue
        if not matched:
            continue
        try:
            # regex/custom 类型把 match/返回值作为第二参数传入；
            # exact/prefix/substring 只传 ctx
            if spec.match_type in ("regex", "custom"):
                ok = await spec.handler(ctx, extra)
            else:
                ok = await spec.handler(ctx)
        except Exception as e:
            if _state.bot_logger:
                _state.bot_logger.info(f"[指令] {spec.name} 处理异常: {e}")
                import traceback
                traceback.print_exc()
            return True
        if ok:
            return True
    return False
