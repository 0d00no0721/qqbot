# -*- coding: utf-8 -*-
"""群聊天短队列：明确提问 FIFO、闲聊合并、跨群轮转。"""

import asyncio
import logging
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from scripts.request_limits import limits, RequestState, request_state


logger = logging.getLogger("qqbot")


@dataclass
class Job:
    websocket: object
    data: dict
    runner: object
    explicit: bool
    settings: dict
    received: float
    deadline: float
    notified: bool = False
    cleanup: object = None

    @property
    def gid(self):
        return str(self.data["group_id"])


class MessageQueue:
    def __init__(self, send, config=limits, clock=time.monotonic):
        self.send = send
        self.config = config
        self.clock = clock
        self.pending = OrderedDict()
        self.active = {}
        self.groups_active = set()
        self.notices = {}
        self.cleanup_tasks = {}
        self.cooldowns = OrderedDict()
        self.timer = None
        self.closed = False

    @property
    def pending_count(self):
        return sum(len(jobs) for jobs in self.pending.values())

    def notice(self, job, kind):
        cfg = job.settings
        if not job.explicit or not cfg["notices_enabled"] or self.closed:
            return
        if kind == "wait":
            if job.notified:
                return
            job.notified = True
        key = (job.gid, str(job.data["user_id"]))
        now = self.clock()
        if self.cooldowns.get(key, 0) > now or len(self.notices) >= self.config.get()["max_message_tasks"]:
            return
        self.cooldowns[key] = now + cfg["notice_cooldown_seconds"]
        self.cooldowns.move_to_end(key)
        while len(self.cooldowns) > 1000:
            self.cooldowns.popitem(last=False)

        async def send():
            try:
                await self.send(job.websocket, job.data["group_id"], cfg[kind + "_message"],
                                at_user=str(job.data["user_id"]))
            except Exception:
                logger.info("[消息队列] 提示发送失败")
        task = asyncio.create_task(send())
        self.notices[task] = job.websocket
        task.add_done_callback(lambda done: self.notices.pop(done, None))

    def reject(self, websocket, data):
        if not data.get("group_id") or not data.get("user_id"):
            return
        now = self.clock()
        job = Job(websocket, data, None, True, self.config.get(data["group_id"]), now, now)
        self.notice(job, "full")

    def submit(self, websocket, data, runner, explicit=False, cleanup=None):
        if self.closed:
            return False
        self.tick()
        now = self.clock()
        cfg = self.config.get(data["group_id"])
        ttl = cfg["queue_wait_seconds"] if explicit else cfg["casual_wait_seconds"]
        received = data.get("_received_monotonic", now)
        job = Job(websocket, dict(data), runner, explicit, cfg, now, received + ttl, cleanup=cleanup)
        if job.deadline <= now:
            self.notice(job, "expired")
            logger.info("[消息队列] outcome=expired group=%s", job.gid)
            return False
        gid = job.gid
        if gid not in self.groups_active and len(self.active) < self.config.get()["max_model_calls"]:
            self._start(job)
            return True
        jobs = self.pending.setdefault(gid, deque())
        if not explicit:
            for previous in list(jobs):
                if not previous.explicit:
                    jobs.remove(previous)
                    self._dispose(previous)
                    logger.info("[消息队列] outcome=coalesced group=%s", gid)
        if ((explicit and sum(j.explicit for j in jobs) >= cfg["max_pending_per_group"])
                or self.pending_count >= self.config.get()["max_pending"]):
            self.notice(job, "full")
            logger.info("[消息队列] outcome=full group=%s", gid)
            if not jobs:
                self.pending.pop(gid, None)
            self._schedule_timer()
            return False
        jobs.append(job)
        self._drain()
        self._schedule_timer()
        return True

    def _dispose(self, job):
        if job.cleanup is not None:
            cleanup, job.cleanup = job.cleanup, None
            task = cleanup()
            if task is not None and not task.done():
                self.cleanup_tasks[task] = job.websocket
                task.add_done_callback(lambda done: self.cleanup_tasks.pop(done, None))

    def _start(self, job):
        self.groups_active.add(job.gid)
        started = self.clock()
        wait = started - job.received
        state = RequestState(remaining_wait=max(0, job.deadline - started),
                             on_wait=lambda: self.notice(job, "wait"),
                             wait_notice_seconds=job.settings["wait_notice_seconds"])

        async def run():
            token = request_state.set(state)
            outcome = "completed"
            try:
                await job.runner()
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as exc:
                outcome = "error"
                logger.warning("[消息队列] 请求失败: %s", type(exc).__name__)
            finally:
                if state.admission_reason:
                    outcome = state.admission_reason
                    self.notice(job, outcome)
                duration = max(0, self.clock() - started)
                intake = max(0, job.received - job.data.get("_received_monotonic", job.received))
                logger.info("[请求耗时] group=%s queue_ms=%d preprocess_ms=%d model_ms=%d "
                            "total_ms=%d attempts=%d script_attempts=%d outcome=%s", job.gid,
                            int((wait + state.model_wait) * 1000),
                            int(max(0, intake + duration - state.model_wait - state.model_time) * 1000),
                            int(state.model_time * 1000), int((intake + duration + wait) * 1000),
                            state.attempts, state.script_attempts, outcome)
                request_state.reset(token)

        task = asyncio.create_task(run())
        self.active[task] = job

        def finished(done):
            self._dispose(job)
            self.active.pop(done, None)
            self.groups_active.discard(job.gid)
            if not self.closed:
                self.tick()
        task.add_done_callback(finished)

    def _drain(self):
        while len(self.active) < self.config.get()["max_model_calls"]:
            available = next((gid for gid in self.pending if gid not in self.groups_active), None)
            if available is None:
                break
            jobs = self.pending.pop(available)
            job = next((item for item in jobs if item.explicit), jobs[0])
            jobs.remove(job)
            if jobs:
                self.pending[available] = jobs  # 轮转到尾部，避免单群占满全局名额。
            self._start(job)

    def tick(self, drain=True):
        now = self.clock()
        for gid, jobs in list(self.pending.items()):
            for job in list(jobs):
                if job.deadline <= now:
                    jobs.remove(job)
                    self._dispose(job)
                    self.notice(job, "expired")
                    logger.info("[消息队列] outcome=expired group=%s", gid)
                elif job.explicit and not job.notified and now >= job.received + job.settings["wait_notice_seconds"]:
                    self.notice(job, "wait")
            if not jobs:
                self.pending.pop(gid, None)
        if drain and not self.closed:
            self._drain()
        self._schedule_timer()

    def _schedule_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None
        times = []
        for jobs in self.pending.values():
            for job in jobs:
                times.append(job.deadline)
                if job.explicit and not job.notified and job.settings["notices_enabled"]:
                    times.append(job.received + job.settings["wait_notice_seconds"])
        if times and not self.closed:
            self.timer = asyncio.get_running_loop().call_later(max(0, min(times) - self.clock()), self.tick)

    def disconnect(self, websocket):
        cancelled = []
        for task, job in list(self.active.items()):
            if job.websocket is websocket:
                self._dispose(job)
                task.cancel()
                cancelled.append(task)
        for task, owner in list(self.notices.items()):
            if owner is websocket:
                task.cancel()
                cancelled.append(task)
        for gid, jobs in list(self.pending.items()):
            for job in jobs:
                if job.websocket is websocket:
                    self._dispose(job)
            remaining = deque(job for job in jobs if job.websocket is not websocket)
            if remaining:
                self.pending[gid] = remaining
            else:
                self.pending.pop(gid, None)
        self._schedule_timer()
        cancelled.extend(task for task, owner in self.cleanup_tasks.items() if owner is websocket)
        return cancelled

    async def close(self):
        self.closed = True
        for jobs in self.pending.values():
            for job in jobs:
                self._dispose(job)
        self.pending.clear()
        self._schedule_timer()
        tasks = list(self.active) + list(self.notices)
        for job in self.active.values():
            self._dispose(job)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*list(self.cleanup_tasks), return_exceptions=True)
