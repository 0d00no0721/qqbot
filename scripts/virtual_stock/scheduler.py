#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 定时任务调度器。

沿用 reverse_bot.py 的 asyncio.create_task 模式，不引入第三方调度框架。

任务清单：
    ── 间隔型 ──
    每 10 分钟  refresh_prices       刷新股价
    每 10 分钟  check_liquidation     爆仓检查（需广播）
    每 10 分钟  check_stock_split     拆股检查（需广播）
    每 30 分钟  recover_stamina       体力恢复

    ── 定点型（每 5 分钟轮询一次是否到点） ──
    00:00       charge_leverage_interest   杠杆日息扣除
    00:05       generate_leaderboard       富豪榜（需广播）
    23:30       process_daily_close        每日收盘（需广播）
    周日 22:00  process_weekly_dividend     每周分红（需广播）
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Set

from .data import STOCK_CODES, load_group_config
from .engine import refresh_prices
from .risk import check_liquidation
from .events import (
    check_stock_split,
    process_daily_close,
    generate_leaderboard,
    process_weekly_dividend,
)
from .account import (
    recover_stamina_for_all_groups,
    charge_leverage_interest,
)

logger = logging.getLogger("virtual_stock.scheduler")

# 广播回调类型：async def (group_id: str, message: str) -> None
BroadcastCallback = Callable[[str, str], Awaitable[None]]


class VSScheduler:
    """虚拟股定时任务调度器。"""

    def __init__(self, broadcast: BroadcastCallback):
        self._broadcast = broadcast
        self._group_ids: Set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._running = False
        # 定点任务的"今日已执行"标记，防止重复触发
        self._last_run: dict[str, str] = {}  # {task_key: "YYYY-MM-DD"}

    # ========== 群管理 ==========

    def register_group(self, group_id: str) -> None:
        """动态注册新群。如果调度器已启动，立即为其创建任务。"""
        group_id = str(group_id)
        if group_id in self._group_ids:
            return
        self._group_ids.add(group_id)
        logger.info(f"[调度器] 群 {group_id} 已注册")

    def _known_group_ids(self) -> list[str]:
        return list(self._group_ids)

    # ========== 启动 / 停止 ==========

    def start(self) -> None:
        """启动所有定时协程。应在 asyncio event loop 中调用。"""
        if self._running:
            return
        self._running = True

        # 间隔型任务（全局，跨所有群）
        self._tasks.append(asyncio.create_task(self._loop_prices()))
        self._tasks.append(asyncio.create_task(self._loop_liquidation()))
        self._tasks.append(asyncio.create_task(self._loop_split()))
        self._tasks.append(asyncio.create_task(self._loop_stamina()))

        # 定点型任务（全局轮询）
        self._tasks.append(asyncio.create_task(self._loop_daily_interest()))
        self._tasks.append(asyncio.create_task(self._loop_daily_leaderboard()))
        self._tasks.append(asyncio.create_task(self._loop_daily_close()))
        self._tasks.append(asyncio.create_task(self._loop_weekly_dividend()))

        logger.info("[调度器] 全部 8 个定时任务已启动")

    def stop(self) -> None:
        """取消所有定时协程。"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("[调度器] 所有定时任务已停止")

    # ========== 间隔型协程 ==========

    async def _loop_prices(self) -> None:
        """每 10 分钟刷新所有群的股价。"""
        await asyncio.sleep(60)  # 启动后等 1 分钟再首次执行
        while self._running:
            for gid in self._known_group_ids():
                try:
                    new_prices = refresh_prices(gid)
                    logger.debug(f"[调度器] 群 {gid} 股价已刷新: {new_prices}")
                except Exception as e:
                    logger.error(f"[调度器] 群 {gid} 刷新股价失败: {e}")
            await asyncio.sleep(600)

    async def _loop_liquidation(self) -> None:
        """每 10 分钟检查所有群的爆仓。"""
        await asyncio.sleep(120)  # 比股价刷新晚 1 分钟
        while self._running:
            for gid in self._known_group_ids():
                try:
                    events = check_liquidation(gid)
                    for ev in events:
                        await self._broadcast(gid, ev.message)
                        logger.info(f"[调度器] 群 {gid} 爆仓广播: {ev.user_id} {ev.stock_code}")
                except Exception as e:
                    logger.error(f"[调度器] 群 {gid} 爆仓检查失败: {e}")
            await asyncio.sleep(600)

    async def _loop_split(self) -> None:
        """每 10 分钟检查所有群的拆股。"""
        await asyncio.sleep(180)  # 比爆仓检查晚 1 分钟
        while self._running:
            for gid in self._known_group_ids():
                for code in STOCK_CODES:
                    try:
                        event = check_stock_split(gid, code)
                        if event:
                            await self._broadcast(gid, event.message)
                            logger.info(f"[调度器] 群 {gid} 拆股: {code}")
                    except Exception as e:
                        logger.error(f"[调度器] 群 {gid} 拆股检查 {code} 失败: {e}")
            await asyncio.sleep(600)

    async def _loop_stamina(self) -> None:
        """每 30 分钟恢复所有群所有用户的体力。"""
        await asyncio.sleep(300)
        while self._running:
            try:
                gids = self._known_group_ids()
                if gids:
                    recover_stamina_for_all_groups(gids)
                    logger.debug(f"[调度器] 体力恢复完成，覆盖 {len(gids)} 个群")
            except Exception as e:
                logger.error(f"[调度器] 体力恢复失败: {e}")
            await asyncio.sleep(1800)

    # ========== 定点型协程 ==========

    async def _loop_daily_interest(self) -> None:
        """每天 00:00 扣除杠杆日息。"""
        while self._running:
            if self._is_time("daily_interest", 0, 0):
                for gid in self._known_group_ids():
                    try:
                        total = charge_leverage_interest(gid)
                        if total > 0:
                            logger.info(f"[调度器] 群 {gid} 杠杆日息扣除: {total}")
                    except Exception as e:
                        logger.error(f"[调度器] 群 {gid} 日息扣除失败: {e}")
            await asyncio.sleep(300)

    async def _loop_daily_leaderboard(self) -> None:
        """每天 00:05 发布富豪榜。"""
        while self._running:
            if self._is_time("daily_leaderboard", 0, 5):
                for gid in self._known_group_ids():
                    try:
                        result = generate_leaderboard(gid)
                        await self._broadcast(gid, result.message)
                        logger.info(f"[调度器] 群 {gid} 富豪榜已发布")
                    except Exception as e:
                        logger.error(f"[调度器] 群 {gid} 富豪榜失败: {e}")
            await asyncio.sleep(300)

    async def _loop_daily_close(self) -> None:
        """每天 23:30 收盘。"""
        while self._running:
            if self._is_time("daily_close", 23, 30):
                for gid in self._known_group_ids():
                    try:
                        report = process_daily_close(gid)
                        await self._broadcast(gid, report.message)
                        logger.info(f"[调度器] 群 {gid} 收盘报告已发布")
                    except Exception as e:
                        logger.error(f"[调度器] 群 {gid} 收盘失败: {e}")
            await asyncio.sleep(300)

    async def _loop_weekly_dividend(self) -> None:
        """每周日 22:00 分红。"""
        while self._running:
            now = datetime.now()
            # isoweekday: 1=周一 ... 7=周日
            if now.isoweekday() == 7 and self._is_time("weekly_dividend", 22, 0):
                for gid in self._known_group_ids():
                    try:
                        events = process_weekly_dividend(gid)
                        for ev in events:
                            await self._broadcast(gid, ev.message)
                            logger.info(f"[调度器] 群 {gid} 分红: {ev.stock_code}")
                    except Exception as e:
                        logger.error(f"[调度器] 群 {gid} 分红失败: {e}")
            await asyncio.sleep(300)

    # ========== 工具函数 ==========

    def _is_time(self, task_key: str, hour: int, minute: int) -> bool:
        """
        检查当前时间是否到了指定时刻（5 分钟窗口内），且今日未执行过。
        精度：±5 分钟（每 5 分钟轮询一次）。
        """
        now = datetime.now()
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 5 分钟窗口：target_time <= now < target_time + 5min
        if not (target_time <= now < target_time + timedelta(minutes=5)):
            return False

        # 防止同一天重复执行
        today_str = now.strftime("%Y-%m-%d")
        if self._last_run.get(task_key) == today_str:
            return False

        self._last_run[task_key] = today_str
        return True


# ========== 全局单例 ==========

_scheduler: VSScheduler | None = None


def start_scheduler(broadcast: BroadcastCallback) -> VSScheduler:
    """
    启动虚拟股定时任务调度器。
    broadcast: 异步回调函数，签名 async (group_id: str, message: str) -> None。
    返回调度器实例。
    """
    global _scheduler
    if _scheduler is not None:
        logger.warning("[调度器] 已在运行，忽略重复启动")
        return _scheduler

    _scheduler = VSScheduler(broadcast)
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    """停止虚拟股定时任务调度器。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None


def register_group(group_id: str) -> None:
    """向调度器注册新群。"""
    global _scheduler
    if _scheduler is not None:
        _scheduler.register_group(group_id)
    else:
        logger.warning(f"[调度器] 尚未启动，群 {group_id} 将在调度器启动后注册")


def get_known_group_ids() -> list[str]:
    """获取已注册的群列表。"""
    global _scheduler
    if _scheduler is not None:
        return _scheduler._known_group_ids()
    return []
