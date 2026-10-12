# -*- coding: utf-8 -*-
"""消息配置、请求耗时与进程内统一模型准入，不创建轮询任务。"""

import asyncio
import contextvars
import functools
import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "message_limits.json"
DEFAULTS = {
    "max_message_tasks": 16, "max_model_calls": 4, "max_pending": 50,
    "max_pending_per_group": 3, "queue_wait_seconds": 180, "casual_wait_seconds": 15,
    "wait_notice_seconds": 10, "notice_cooldown_seconds": 30, "notices_enabled": True,
    "wait_message": "已收到，正在排队，请不用重复发送。",
    "full_message": "现在排队的人有点多，请稍后再试。",
    "expired_message": "这条消息等待过久还没开始处理，请重新 @ 我。",
}
GLOBAL_KEYS = {"max_message_tasks", "max_model_calls", "max_pending"}
logger = logging.getLogger("qqbot")


class LimitsConfig:
    def __init__(self, path=CONFIG_FILE):
        self.path = Path(path)
        self.signature = None
        self.defaults = dict(DEFAULTS)
        self.groups = {}
        self.lock = threading.RLock()

    @staticmethod
    def validate(raw, base, group=False):
        if not isinstance(raw, dict):
            raise ValueError("配置项必须是对象")
        result = dict(base)
        for key, value in raw.items():
            if key not in DEFAULTS or (group and key in GLOBAL_KEYS):
                raise ValueError(f"不支持的配置项: {key}")
            if key.endswith("message"):
                valid = isinstance(value, str) and 0 < len(value.strip()) <= 500
            elif key == "notices_enabled":
                valid = isinstance(value, bool)
            else:
                low = 0 if key == "max_pending_per_group" else 0.001
                valid = (not isinstance(value, bool) and isinstance(value, (int, float))
                         and math.isfinite(value) and value >= low)
                if key.startswith("max_"):
                    valid = valid and value == int(value)
            if not valid:
                raise ValueError(f"配置值无效: {key}")
            result[key] = value
        return result

    def get(self, gid=None):
        with self.lock:
            try:
                stat = self.path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                signature = (None, None)
            if signature != self.signature:
                self.signature = signature
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict) or not isinstance(raw.get("groups", {}), dict):
                        raise ValueError("根节点及 groups 必须是对象")
                    defaults = self.validate(raw.get("defaults", {}), DEFAULTS)
                    groups = {str(g): self.validate(values, defaults, True)
                              for g, values in raw.get("groups", {}).items()}
                    self.defaults, self.groups = defaults, groups
                except FileNotFoundError:
                    self.defaults, self.groups = dict(DEFAULTS), {}
                except (OSError, ValueError) as exc:
                    logger.warning("[消息队列] 配置无效，沿用上次有效值: %s", exc)
            return dict(self.groups.get(str(gid), self.defaults))


limits = LimitsConfig()


@dataclass
class RequestState:
    remaining_wait: float = 180
    model_wait: float = 0
    model_time: float = 0
    attempts: int = 0
    admission_reason: str = ""
    generation_started: bool = False
    on_wait: object = None
    wait_notice_seconds: float = 10


request_state = contextvars.ContextVar("qqbot_request_state", default=None)


class ModelAdmissionError(RuntimeError):
    pass


class ModelGate:
    """进程级计数，兼容独立线程中的事件循环；等待者数量也有上限。"""
    def __init__(self, config=limits):
        self.config = config
        self.active = 0
        self.waiters = deque()
        self.lock = threading.RLock()

    def _wake(self):
        maximum = self.config.get()["max_model_calls"]
        while self.waiters and self.active < maximum:
            waiter = self.waiters.popleft()
            loop, future, _ = waiter
            if future.done() or loop.is_closed():
                continue
            waiter[2] = True
            self.active += 1
            try:
                loop.call_soon_threadsafe(lambda f=future: f.set_result(None) if not f.done() else None)
            except RuntimeError:  # 另一线程恰好关闭事件循环。
                waiter[2] = False
                self.active -= 1

    async def acquire(self, state):
        loop = asyncio.get_running_loop()
        began = time.monotonic()
        with self.lock:
            cfg = self.config.get()
            if self.active < cfg["max_model_calls"] and not self.waiters:
                self.active += 1
                return
            if len(self.waiters) >= cfg["max_pending"]:
                state.admission_reason = "full"
                raise ModelAdmissionError("模型等待槽已满")
            future = loop.create_future()
            waiter = [loop, future, False]
            self.waiters.append(waiter)
        timer = (loop.call_later(state.wait_notice_seconds, state.on_wait)
                 if state.on_wait else None)
        try:
            await asyncio.wait_for(asyncio.shield(future), max(0, state.remaining_wait))
        except BaseException as exc:
            with self.lock:
                future.cancel()
                if waiter[2]:
                    self.active -= 1
                else:
                    self.waiters.remove(waiter)
                self._wake()
            if isinstance(exc, asyncio.TimeoutError):
                state.admission_reason = "expired"
                raise ModelAdmissionError("等待模型名额超时") from exc
            raise
        finally:
            waited = time.monotonic() - began
            state.model_wait += waited
            # 已开始生成的多阶段请求不再使用旧的排队截止时间。
            if not state.generation_started:
                state.remaining_wait = max(0, state.remaining_wait - waited)
            if timer:
                timer.cancel()

    def release(self):
        with self.lock:
            self.active -= 1
            self._wake()


model_gate = ModelGate()


def note_model_attempt():
    state = request_state.get()
    if state:
        state.attempts += 1


def limit_model_calls(fn):
    """必须放在生成超时装饰器之外，排队不会消耗生成预算。"""
    @functools.wraps(fn)
    async def limited(*args, **kwargs):
        state = request_state.get()
        token = None
        if state is None:
            state = RequestState(remaining_wait=limits.get()["queue_wait_seconds"])
            token = request_state.set(state)
        try:
            await model_gate.acquire(state)
            state.generation_started = True
            state.remaining_wait = limits.get()["queue_wait_seconds"]
            began = time.monotonic()
            try:
                return await fn(*args, **kwargs)
            finally:
                state.model_time += time.monotonic() - began
                model_gate.release()
        finally:
            if token is not None:
                request_state.reset(token)
    return limited
