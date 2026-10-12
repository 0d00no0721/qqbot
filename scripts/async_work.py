# -*- coding: utf-8 -*-
"""分类/图片的受限工作线程：提交前占位，实际工作结束才释放。"""

import asyncio
import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "worker_limits.json"
DEFAULTS = {"classifier_pending": 4, "image_workers": 2, "image_pending": 2,
            "download_concurrency": 2, "download_timeout_seconds": 10,
            "worker_timeout_seconds": 30}
_signature = None
_config = dict(DEFAULTS)
_workers = {}
_downloads = 0
_download_tasks = set()
_closing = False
_guard = threading.RLock()
logger = logging.getLogger("qqbot")


def settings():
    global _signature, _config
    with _guard:
        try:
            stat = CONFIG_FILE.stat()
            signature = (str(CONFIG_FILE), stat.st_mtime_ns, stat.st_size)
        except OSError:
            signature = (str(CONFIG_FILE), None)
        if signature != _signature:
            _signature = signature
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("根节点必须是对象")
                result = dict(DEFAULTS)
                for key, default in DEFAULTS.items():
                    value = raw.get(key, default)
                    lower = 0 if key.endswith("pending") else 1
                    if (isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value) or value < lower
                            or (not key.endswith("seconds") and int(value) != value)):
                        raise ValueError(f"{key} 数值无效")
                    result[key] = value if key.endswith("seconds") else int(value)
                _config = result
            except FileNotFoundError:
                _config = dict(DEFAULTS)
            except (OSError, ValueError) as exc:
                logger.warning("[异步工作] 配置无效，沿用上次有效值: %s", exc)
        return dict(_config)


class BoundedWorker:
    def __init__(self, workers, name):
        self.size = workers
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self.futures = set()
        self.lock = threading.Lock()
        self.closed = False

    async def run(self, fn, *args, pending=0, timeout=30):
        with self.lock:
            if self.closed or len(self.futures) >= self.size + pending:
                logger.info("[异步工作] 工作槽已满，跳过增强处理")
                return None
            future = self.executor.submit(fn, *args)
            self.futures.add(future)

        def finished(completed):
            with self.lock:
                self.futures.discard(completed)

        future.add_done_callback(finished)
        wrapped = asyncio.wrap_future(future)
        # 调用方不再等待时仍消费异常；名额由底层 future 的完成回调释放。
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout)
        except asyncio.TimeoutError:
            logger.info("[异步工作] 等待结果超时，运行中的线程仍保留名额")
            return None
        except Exception as exc:
            logger.warning("[异步工作] 处理失败，已降级: %s", type(exc).__name__)
            return None

    async def close(self):
        with self.lock:
            self.closed = True
        await asyncio.to_thread(self.executor.shutdown, wait=True, cancel_futures=True)


async def run_work(kind, fn, *args):
    cfg = settings()
    size = 1 if kind == "classifier" else cfg["image_workers"]
    with _guard:
        if _closing:
            return None
        worker = _workers.get(kind)
        if worker and worker.size != size:
            with worker.lock:
                if worker.futures:
                    return None  # 改小并发时等待已有工作结束，不另开一组线程。
                worker.closed = True
            worker.executor.shutdown(wait=False)
            worker = None
        if worker is None:
            worker = _workers[kind] = BoundedWorker(size, "qqbot-" + kind)
    return await worker.run(fn, *args, pending=cfg[kind + "_pending"],
                            timeout=cfg["worker_timeout_seconds"])


def take_download(task=None):
    global _downloads
    with _guard:
        if _closing or _downloads >= settings()["download_concurrency"]:
            return False
        _downloads += 1
        if task is not None:
            _download_tasks.add(task)
        return True


def release_download(task=None):
    global _downloads
    with _guard:
        _downloads -= 1
        _download_tasks.discard(task)


def start_workers():
    global _closing
    _closing = False


async def close_workers():
    global _closing
    with _guard:
        _closing = True
        workers = list(_workers.values())
        _workers.clear()
        downloads = [task for task in _download_tasks if task is not asyncio.current_task()]
    for task in downloads:
        task.cancel()
    await asyncio.gather(*downloads, return_exceptions=True)
    await asyncio.gather(*(worker.close() for worker in workers))
