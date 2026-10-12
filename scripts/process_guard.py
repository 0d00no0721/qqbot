# -*- coding: utf-8 -*-
"""独立模型脚本的有界执行与回收，仅在调用时创建任务。"""

import asyncio
import logging
import time


_reaping = set()


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
        proc = await asyncio.wait_for(asyncio.shield(creating),
                                      max(0, work_deadline - time.monotonic()))
        communication = asyncio.create_task(proc.communicate(input=input))
        stdout, stderr = await asyncio.wait_for(asyncio.shield(communication),
                                                max(0, work_deadline - time.monotonic()))
        return proc.returncode, stdout, stderr
    except BaseException:
        cleaning = asyncio.create_task(reap())
        _reaping.add(cleaning)

        def finished(task):
            _reaping.discard(task)
            if not task.cancelled() and task.exception():
                logging.getLogger("qqbot").warning("[模型进程] 回收失败: %s", task.exception())

        cleaning.add_done_callback(finished)
        try:
            await asyncio.wait_for(asyncio.shield(cleaning), max(0, deadline - time.monotonic()))
        except (asyncio.TimeoutError, asyncio.CancelledError):
            logging.getLogger("qqbot").warning("[模型进程] 创建/回收尚未结束，保留退出回调")
        raise
