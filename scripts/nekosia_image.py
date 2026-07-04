#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Nekosia API 猫娘图片获取模块
=============================
封装 Nekosia API 调用，提供获取随机猫娘图片 URL 的功能。
API 文档: https://nekosia.cat/documentation

使用方式:
    from scripts.nekosia_image import fetch_catgirl_image
    url = await fetch_catgirl_image()   # 返回 str 或 None
"""

import httpx
from typing import Optional

# Nekosia API 端点（catgirl 分类）
_CATGIRL_API = "https://api.nekosia.cat/api/v1/images/catgirl?count=1"
_TIMEOUT = 15.0


async def fetch_catgirl_image() -> Optional[str]:
    """
    从 Nekosia API 获取一张随机猫娘图片的 URL。

    API 响应结构:
    {
        "success": true,
        "image": {
            "original": { "url": "https://cdn.nekosia.cat/images/catgirl/..." }
        }
    }

    返回:
        str  — 图片直链 URL
        None — 获取失败
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_CATGIRL_API)
            if resp.status_code != 200:
                return None

            data = resp.json()
            if not data.get("success"):
                return None

            # 提取 URL: data["image"]["original"]["url"]
            image_url = data.get("image", {}).get("original", {}).get("url")
            return image_url if image_url else None

    except (httpx.TimeoutException, httpx.RequestError, Exception):
        return None