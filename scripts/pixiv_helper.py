#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pixiv 排行榜图片获取模块
=======================
封装 Pixiv API 调用，提供从排行榜获取随机图片 URL 的功能。

使用方式:
    from scripts.pixiv_helper import fetch_random_pixiv_image
    result = await fetch_random_pixiv_image("daily")  # 返回 dict 或 None

配置:
    修改下方 PHPSESSID 和 PROXY 常量来适配环境。
"""

import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional

# ====== 配置 ======
PHPSESSID = "124343876_3D5gVjfHTOEntSBDqnEsYKETcFb9RhXs"
PROXY = None  # 例如 "http://127.0.0.1:15715"

# 排行榜数据延迟一天（今日显示昨日排名）
_RANKING_DATE_OFFSET = timedelta(days=1)

# 各类榜单抽取范围（放宽至前50）
DAILY_TOP_N = 50
WEEKLY_TOP_N = 50
MONTHLY_TOP_N = 50

# 榜单类型 → (RankType key, topN, 中文名称)
RANKING_CONFIG = {
    "daily":   ("daily",  DAILY_TOP_N,   "日榜"),
    "weekly":  ("weekly", WEEKLY_TOP_N,  "周榜"),
    "monthly": ("monthly", MONTHLY_TOP_N, "月榜"),
}
# =========================

# 榜单对应的 Pixiv 页面 URL
RANKING_PAGE_URL = {
    "daily":   "https://www.pixiv.net/ranking.php?mode=daily",
    "weekly":  "https://www.pixiv.net/ranking.php?mode=weekly",
    "monthly": "https://www.pixiv.net/ranking.php?mode=monthly",
}

# API 实例缓存（懒加载单例）
_api_instance = None


def _get_pixiv_api():
    """获取 PixivApi 单例（首次调用时初始化）"""
    global _api_instance
    if _api_instance is None:
        from pixivtools.pixiv_api import PixivApi
        from pixivtools.pixiv_cfg import ApiMetaArgument

        meta = ApiMetaArgument(phpsessid=PHPSESSID, proxy=PROXY or "")
        _api_instance = PixivApi(meta)
    return _api_instance


def _sync_fetch(rank_type: str, rank_cn: str, top_n: int, date_int: int):
    """在子线程中同步执行 API 调用"""
    api = _get_pixiv_api()
    from pixivtools.pixiv_api.struct import RankType

    rank_map = {
        "daily": RankType.DAILY,
        "weekly": RankType.WEEKLY,
        "monthly": RankType.MONTHLY,
    }

    artworks = api.get_artworks_by_rank(
        rank_type=rank_map[rank_type],
        date=date_int,
        page=1,
        options={"only_r18": False, "only_non_r18": True},
    )
    if not artworks:
        return None

    ids = list(artworks.keys())
    top_ids = ids[:top_n]
    if not top_ids:
        return None

    pick_id = random.choice(top_ids)
    info = artworks[pick_id]

    # 获取第一张图片的原图 URL
    urls = list(info.image_download_urls)
    if not urls:
        return None

    image_url = urls[0].original

    return {
        "image_url": image_url,
        "artwork_id": info.artwork_id,
        "title": info.title,
        "user_name": info.user_name,
        "user_id": info.user_id,
        "rank_type": rank_cn,
        "rank_list_url": RANKING_PAGE_URL[rank_type],
    }


async def fetch_random_pixiv_image(rank_type: str) -> Optional[dict]:
    """
    获取指定榜单的随机图片信息。

    参数:
        rank_type: "daily" / "weekly" / "monthly"

    返回:
        {
            "image_url": str,       # 图片下载直链
            "artwork_id": int,      # 作品 ID
            "title": str,           # 作品标题
            "user_name": str,       # 作者名
            "user_id": int,         # 作者 ID
            "rank_type": str,       # 榜单类型中文名（日榜/周榜/月榜）
            "rank_list_url": str,   # 完整榜单 URL（给用户提供的链接）
        }
        或 None（获取失败时）
    """
    if rank_type not in RANKING_CONFIG:
        return None

    rank_key, top_n, rank_cn = RANKING_CONFIG[rank_type]
    date_int = int((datetime.now() - _RANKING_DATE_OFFSET).strftime("%Y%m%d"))

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            None, _sync_fetch, rank_key, rank_cn, top_n, date_int
        )
        return result
    except Exception:
        return None


async def download_pixiv_image(image_url: str) -> Optional[bytes]:
    """用 pixivtools 的 Api (带 PHPSESSID 鉴权) 下载图片二进制数据"""
    loop = asyncio.get_event_loop()
    try:
        api = _get_pixiv_api()
        img_bytes = await loop.run_in_executor(None, api.get_image, image_url)
        return img_bytes
    except Exception:
        return None