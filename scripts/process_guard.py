# -*- coding: utf-8 -*-
"""独立模型脚本的有界执行与回收，仅在调用时创建任务。"""

import asyncio
import contextvars
import logging
import time


_reaping = set()
# 可选的调用方资源归属通知；不等待、不创建额外任务。
cleanup_observer = contextvars.ContextVar("model_process_cleanup_observer", default=None)


async def _wait_until(task, deadline):
    # Python 3.10 的 wait_for 在 future 完成与外部取消同时到达时可能吞掉取消。
    # wait 不取消底层任务，取消总会传播；底层进程统一由下方 reap 负责。
    done, _ = await asyncio.wait({task}, timeout=max(0, deadline - time.monotonic()))
    if not done:
        raise asyncio.TimeoutError
    return task.result()


async def run_process(*args, input=b"", timeout=60.0, cleanup_seconds=2.0):
    """返回 (returncode, stdout, stderr)，超时/取消时杀死并回收自己创建的进程。

    总时限中预留清理时间。若操作系统迟迟未完成创建，保留回收回调，
    创建一旦返回就终止进程，不把遗留进程交给下一轮请求。
    """
    deadline = time.monotonic() + timeout
    reserve = min(cleanup_seconds, timeout / 2)
    work_deadline = deadline - reserve
    creating = asyncio.create_task(asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    ))
    proc = None
    communication = None

    async def reap():
        child = proc
        if child is None:
            try:
                child = await creating
            except Exception:
                return
        if child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
        # communicate 负责排空输出和关闭 stdin；不能在取消时丢弃其任务。
        if communication is None:
            await child.communicate()
        else:
            await communication
        await child.wait()

    try:
        proc = await _wait_until(creating, work_deadline)
        communication = asyncio.create_task(proc.communicate(input=input))
        stdout, stderr = await _wait_until(communication, work_deadline)
        return proc.returncode, stdout, stderr
    except BaseException:
        cleaning = asyncio.create_task(reap())
        _reaping.add(cleaning)
        observer = cleanup_observer.get()
        if observer is not None:
            try:
                observer(cleaning)
            except Exception:
                logging.getLogger("qqbot").warning("[模型进程] 回收通知失败，继续回收进程")

        def finished(task):
            _reaping.discard(task)
            if not task.cancelled() and task.exception():
                logging.getLogger("qqbot").warning("[模型进程] 回收失败: %s", task.exception())

        cleaning.add_done_callback(finished)
        try:
            await _wait_until(cleaning, deadline)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logging.getLogger("qqbot").warning("[模型进程] 创建/回收尚未结束，保留退出回调")
        raise
