"""
神秘数字 — 随机生成 JMComic ID 并验证有效性
"""

import random
import asyncio
import logging
from typing import Optional
from pathlib import Path

from jmcomic import JmOption, MissingAlbumPhotoException

bot_logger = logging.getLogger("qqbot")

# 最大重试次数
MAX_RETRIES = 3

# jmcomic 配置路径（同目录）
_OPTION_PATH = Path(__file__).parent / 'jm_option.yml'


def _generate_number() -> str:
    """随机生成一个五位数、六位数或七位数（七位数概率 <= 2%）"""
    r = random.randint(1, 100)
    if r <= 2:
        # 七位数，上限 1399999
        return str(random.randint(1000000, 1399999))
    elif r <= 51:
        # 五位数
        return str(random.randint(10000, 99999))
    else:
        # 六位数
        return str(random.randint(100000, 999999))


def _check_single(id_str: str, option: JmOption):
    """
    检查一个数字 ID 是否有效。

    返回:
        ("valid", True)    - ID 有效
        ("invalid", False) - ID 无效
        ("error", None)    - 网络异常
    """
    id_str = id_str.strip()
    if not id_str.isdigit():
        return ("invalid", False)

    try:
        client = option.build_jm_client()
        client.get_album_detail(id_str)
        return ("valid", True)
    except MissingAlbumPhotoException:
        return ("invalid", False)
    except Exception:
        return ("error", None)


def find_valid_number():
    """
    尝试找到 MAX_RETRIES 次有效 ID。
    每次生成一个随机数字并验证。

    返回:
        ("success", id_str)  - 找到了有效 ID
        ("network_error",)   - 所有尝试都遇到了网络问题
        ("no_luck",)         - 所有尝试的 ID 都无效
    """
    option = JmOption.from_file(str(_OPTION_PATH))
    has_any_error = False

    for attempt in range(1, MAX_RETRIES + 1):
        num = _generate_number()
        bot_logger.info(f"[神秘数字] 第 {attempt} 次尝试: {num}")

        status, result = _check_single(num, option)

        if status == "valid":
            bot_logger.info(f"[神秘数字] 找到有效 ID: {num}")
            return ("success", num)

        if status == "error":
            has_any_error = True

    # MAX_RETRIES 次都没找到
    if has_any_error:
        return ("network_error",)
    return ("no_luck",)


async def find_valid_number_async():
    """异步包装，在线程池中执行（避免阻塞事件循环）"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, find_valid_number)