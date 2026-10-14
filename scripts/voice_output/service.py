# -*- coding: utf-8 -*-
"""事件驱动的语音队列。先准入再创建任务，同群串行，空闲时没有后台任务。"""

import asyncio
import copy
import logging
import time
import weakref
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable

import httpx

from .config import LIMITS, VoiceConfig
from .providers import PROVIDERS, VoiceError
from .transport import send_record


@dataclass
class VoiceJob:
    owner: object
    group_id: str
    user_id: str
    text: str
    voice: dict
    napcat: dict
    limits: dict
    created: float
    deadline: float
    on_failure: Callable
    reservation: int = 0


class VoiceService:
    def __init__(self, config_path, *, logger=None, clock=time.monotonic,
                 providers=None, sender=send_record, client=None):
        self.logger = logger or logging.getLogger("qqbot")
        self.config = VoiceConfig(config_path, self.logger)
        self.clock = clock
        self.providers = dict(PROVIDERS if providers is None else providers)
        self.sender = sender
        self._client = client
        self._pending = deque()
        self._active = {}
        self._groups = set()
        self._rates = OrderedDict()
        self._seen = OrderedDict()
        self._notices = OrderedDict()
        self._closed_owners = weakref.WeakSet()
        self._closed = False
        self._reserved = 0
        self._blocked = set()
        self._config_stamp = None

    def settings(self, group_id):
        config = self.config.load()
        if self.config._stamp != self._config_stamp:
            self._blocked.clear()
            self._config_stamp = self.config._stamp
        return config, VoiceConfig.for_group(config, group_id)

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=None, follow_redirects=False,
                limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            )
        return self._client

    def _alive(self, owner):
        return not self._closed and owner not in self._closed_owners and not getattr(owner, "closed", False)

    @staticmethod
    def _remember(cache, key, value, capacity):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > capacity:
            cache.popitem(last=False)

    def submit(self, *, owner, group_id, user_id, message_id, text, kind, on_failure,
               config=None, group=None):
        if not self._alive(owner):
            return "disconnected"
        if config is None or group is None:
            config, group = self.settings(group_id)
        if not group["enabled"]:
            return "disabled"
        if not text.strip() or len(text) > group["max_chars"]:
            return "text_length"
        voice = config["voices"].get(group["voice"])
        if not voice or voice["provider"] not in self.providers:
            return "voice_missing"
        if voice.get("endpoint") in self._blocked:
            return "provider_paused"
        limits = config["limits"]
        self._pump()
        now = self.clock()
        gid, uid = str(group_id), str(user_id)
        key = (gid, str(message_id), kind) if message_id is not None else None
        # 每个缓存项保存自己的到期时间；配置变化不会复活已消费的请求。
        for old, expires in list(self._seen.items()):
            if expires <= now:
                self._seen.pop(old, None)
        if key is not None and key in self._seen:
            return "duplicate"
        for rate_key, seconds in (((gid, "*"), group["group_cooldown_seconds"]),
                                  ((gid, uid), group["user_cooldown_seconds"])):
            if now - self._rates.get(rate_key, -float("inf")) < seconds:
                return "cooldown"
        can_start = (len(self._active) < limits["concurrency"] and gid not in self._groups
                     and self._reserved + limits["max_audio_bytes"] <= limits["max_buffer_bytes"])
        if not can_start:
            if len(self._pending) >= limits["max_pending"]:
                return "queue_full"
            if sum(job.group_id == gid for job in self._pending) >= limits["group_pending"]:
                return "queue_full"
        job = VoiceJob(owner, gid, uid, text, copy.deepcopy(voice), dict(config["napcat"]),
                       dict(limits), now, now + limits["queue_timeout_seconds"], on_failure)
        self._remember(self._rates, (gid, "*"), now, limits["state_entries"])
        self._remember(self._rates, (gid, uid), now, limits["state_entries"])
        if key is not None:
            self._remember(self._seen, key, now + limits["dedup_seconds"], limits["state_entries"])
        self._pending.append(job)
        self._pump()
        return "accepted"

    def _pump(self):
        if self._closed:
            return
        config = self.config.load()
        limits = config["limits"] if config else LIMITS
        # 遇到正在发语音的群就跳过，让其他群获得空闲名额。
        for job in list(self._pending):
            if len(self._active) >= limits["concurrency"]:
                break
            if job.group_id in self._groups:
                continue
            if self._reserved + job.limits["max_audio_bytes"] > limits["max_buffer_bytes"]:
                # 上限调小后，旧任务仍使用旧的单条上限；无法容纳时按失败回退。
                if self._active:
                    continue
                reason = "buffer_full"
            else:
                reason = None
            self._pending.remove(job)
            if not self._alive(job.owner):
                continue
            self._groups.add(job.group_id)
            job.reservation = 0 if reason else job.limits["max_audio_bytes"]
            self._reserved += job.reservation
            task = asyncio.create_task(self._run(job, reason))
            self._active[task] = job
            task.add_done_callback(self._done)

    async def _failure(self, job, reason):
        if job.on_failure and self._alive(job.owner):
            try:
                await asyncio.wait_for(job.on_failure(reason), job.limits["send_timeout_seconds"])
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.warning("[语音] 文字回退失败 群=%s", job.group_id)

    async def _run(self, job, reason=None):
        started = self.clock()
        synth_ms = send_ms = 0
        stage = "waiting"
        send_start = None
        work_ended = None
        try:
            if not self._alive(job.owner):
                return
            if reason or started >= job.deadline:
                reason = reason or "expired"
                await self._failure(job, reason)
                return
            if job.voice.get("endpoint") in self._blocked:
                reason = "provider_paused"
                await self._failure(job, reason)
                return
            stage = "synthesis"
            client = self._http()
            audio = await asyncio.wait_for(
                self.providers[job.voice["provider"]](job.text, job.voice, client, job.limits["max_audio_bytes"]),
                job.limits["synthesis_timeout_seconds"],
            )
            synth_ms = int((self.clock() - started) * 1000)
            if not audio.data or len(audio.data) > job.limits["max_audio_bytes"]:
                raise VoiceError("audio_too_large")
            if not self._alive(job.owner):
                return
            # 排队期限只作用于开始合成前，不会截断已经开始的合成或发送。
            stage = "sending"
            send_start = self.clock()
            await asyncio.wait_for(self.sender(client, job.napcat, job.group_id, audio),
                                   job.limits["send_timeout_seconds"])
            send_ms = int((self.clock() - send_start) * 1000)
            reason = "sent"
        except asyncio.CancelledError:
            reason = "cancelled"
            if stage == "synthesis" and job.voice.get("endpoint"):
                self._blocked.add(job.voice["endpoint"])
            raise
        except (asyncio.TimeoutError, httpx.TimeoutException):
            reason = stage + "_timeout"
            work_ended = self.clock()
            # HTTP 取消不代表远端 GPU 推理已经终止。暂停该端点，避免继续堆叠请求。
            if stage == "synthesis" and job.voice.get("endpoint"):
                self._blocked.add(job.voice["endpoint"])
            await self._failure(job, reason)
        except httpx.TransportError as exc:
            reason = type(exc).__name__
            work_ended = self.clock()
            if stage == "synthesis" and job.voice.get("endpoint"):
                self._blocked.add(job.voice["endpoint"])
            await self._failure(job, reason)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, VoiceError) else type(exc).__name__
            work_ended = self.clock()
            await self._failure(job, reason)
        finally:
            end = work_ended if work_ended is not None else self.clock()
            if stage == "synthesis":
                synth_ms = int((end - started) * 1000)
            elif send_start is not None:
                send_ms = int((end - send_start) * 1000)
            self.logger.info("[语音] 群=%s provider=%s 结果=%s 排队=%dms 合成=%dms 发送=%dms 总计=%dms",
                             job.group_id, job.voice["provider"], reason or "disconnected",
                             int((started - job.created) * 1000), synth_ms, send_ms,
                             int((self.clock() - job.created) * 1000))

    def _done(self, task):
        job = self._active.pop(task)
        self._groups.discard(job.group_id)
        self._reserved -= job.reservation
        # 取出异常，避免 task exception was never retrieved；取消也不触发自动重试。
        if not task.cancelled():
            task.exception()
        self._pump()

    async def disconnect(self, owner):
        self._closed_owners.add(owner)
        self._pending = deque(job for job in self._pending if job.owner is not owner)
        tasks = [task for task, job in self._active.items() if job.owner is owner]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self._closed = True
        self._pending.clear()
        tasks = list(self._active)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def drain(self):
        """离线测试/受控退出使用；普通聊天不会等待此函数。"""
        while self._active:
            await asyncio.gather(*list(self._active), return_exceptions=True)
            await asyncio.sleep(0)

    async def reply(self, owner, group_id, user_id, message_id, text, send_text, *, is_at):
        config, group = self.settings(group_id)
        if not group["enabled"] or group["mode"] == "off" or (group["only_at"] and not is_at):
            return await send_text(text)
        if group["mode"] == "text_and_voice":
            # 文字先走既有路径；合成、HTTP 请求和排队都在之后。
            if not await send_text(text):
                return False
            failure = None
        else:
            async def failure(_reason):
                await send_text(text)
        result = self.submit(owner=owner, group_id=group_id, user_id=user_id, message_id=message_id,
                             text=text, kind="reply", on_failure=failure, config=config, group=group)
        if result != "accepted":
            self.logger.info("[语音] 群=%s 未准入=%s", group_id, result)
            if failure and result not in ("duplicate", "disconnected"):
                await failure(result)
        return True

    async def read(self, owner, group_id, user_id, message_id, text, send_text):
        config, group = self.settings(group_id)
        async def notice(text):
            key = (str(group_id), str(user_id))
            now = self.clock()
            if now - self._notices.get(key, -float("inf")) < group["notice_cooldown_seconds"]:
                return
            self._remember(self._notices, key, now, (config or {}).get("limits", LIMITS)["state_entries"])
            await send_text(text)
        if not group["enabled"] or not group["read_command"]:
            await notice("本群未开启朗读功能。")
            return
        async def failure(_reason):
            await notice("这次语音未能完成，请稍后再试。")
        result = self.submit(owner=owner, group_id=group_id, user_id=user_id, message_id=message_id,
                             text=text, kind="read", on_failure=failure, config=config, group=group)
        messages = {
            "text_length": "朗读内容须为 1～%d 字。" % group["max_chars"],
            "cooldown": "朗读请求太频繁，请稍后再试。",
            "queue_full": "语音正在忙，请稍后再试。",
            "voice_missing": "本群音色尚未配置好，请联系管理员。",
            "provider_paused": "语音服务已暂停，请管理员确认服务恢复后重载语音配置。",
        }
        if result in messages:
            await notice(messages[result])
