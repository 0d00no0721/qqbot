# -*- coding: utf-8 -*-
"""已准入聊天的临时图片。预算包含下载中缓冲，空闲不创建任务。"""

import asyncio
import logging
import threading
import weakref

from scripts.request_limits import limits


logger = logging.getLogger("qqbot")


class AttachmentBudget:
    def __init__(self, config=limits):
        self.config = config
        self.used = 0
        self.lock = threading.Lock()

    def take(self, size):
        with self.lock:
            if self.used + size > self.config.get()["max_prefetch_bytes"]:
                return False
            self.used += size
            return True

    def release(self, size):
        with self.lock:
            self.used -= size


attachment_budget = AttachmentBudget()


class Attachment:
    def __init__(self, data, budget):
        self.data = data
        # 图片处理超时/取消后，线程可能仍持有本对象；最后一个持有者退出才退额。
        weakref.finalize(self, budget.release, len(data))


def _encode(attachment):
    from scripts.image_utils import to_data_url
    return to_data_url(attachment.data)


class ImagePrefetch:
    def __init__(self, budget=attachment_budget):
        self.budget = budget
        self.images = []
        self.task = None
        self.closed = False

    def start(self, candidates):
        """调用方先执行触发条件、群额度、贴图过滤、同图去重。"""
        if candidates and not self.closed and self.task is None:
            from scripts.image_utils import fetch_image_async, DEFAULT_MAX_BYTES
            self.task = asyncio.create_task(self._download(candidates, fetch_image_async, DEFAULT_MAX_BYTES))

    async def _download(self, candidates, fetch, maximum):
        # 同时预留下载缓冲与 bytes 转换的空间；完成后只保留实际原图大小。
        reserved = 2 * maximum
        for meta in candidates:
            if self.closed:
                break
            if not self.budget.take(reserved):
                logger.info("[识图] 预取跳过：全局附件缓存已满")
                break
            retained = 0
            data = None
            try:
                data = await fetch(meta, max_bytes=maximum)
                if data and not self.closed:
                    self.images.append(Attachment(data, self.budget))
                    retained = len(data)
                else:
                    logger.info("[识图] 预取失败或下载繁忙，沿用文本链")
            except Exception as exc:
                logger.info("[识图] 预取失败：%s", type(exc).__name__)
            finally:
                data = None
                self.budget.release(reserved - retained)

    async def describe(self, hint):
        from scripts import vision
        from scripts.async_work import run_work
        if self.task is not None:
            await self.task
        for attachment in self.images:
            data_url = await run_work("image", _encode, attachment)
            if not data_url:
                continue
            # 仅 vision 内的模型调用申请名额；图片下载、解码不占模型名额。
            ok, desc = await vision.describe(data_url, hint=hint)
            if ok and desc:
                return [f"[图片: {desc}]"]
        return []

    def close(self):
        self.closed = True
        self.images.clear()
        if self.task is not None and not self.task.done():
            self.task.cancel()
            return self.task
