"""
签到模块 — 每日签到 + 排名 + 连续天数统计
数据文件: sign_in_data.json（与 menu_data.json 同级，在脚本根目录）
"""

import json
import os
from datetime import datetime, date, timedelta
from typing import Dict, Optional, Tuple

# 数据文件路径（脚本所在目录的父目录，即项目根目录）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIGN_IN_DATA_FILE = os.path.join(SCRIPT_DIR, "sign_in_data.json")


def load_sign_in_data() -> Dict:
    """加载签到数据文件"""
    if not os.path.exists(SIGN_IN_DATA_FILE):
        return {}
    try:
        with open(SIGN_IN_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[签到] 读取数据文件失败: {e}")
        return {}


def save_sign_in_data(data: Dict) -> None:
    """保存签到数据文件"""
    with open(SIGN_IN_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def process_sign_in(group_id: str, user_id: str) -> Optional[Dict]:
    """
    处理签到请求。

    参数:
        group_id: 群号字符串
        user_id: 用户QQ号字符串

    返回:
        None                           - 今天已签到，重复
        {"success": True, ...}         - 签到成功，含统计信息
    """
    data = load_sign_in_data()
    today = date.today().isoformat()  # "YYYY-MM-DD"
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # 获取或创建该群该用户的记录
    group_data = data.setdefault(group_id, {})
    record = group_data.get(user_id)

    if record is None:
        record = {
            "total_days": 0,
            "streak": 0,
            "last_sign_date": "",
            "today_sign_at": "",
        }
        group_data[user_id] = record

    # 检查是否今天已签到
    if record.get("last_sign_date") == today:
        return None  # 重复签到

    # 判断是否断签
    if record.get("last_sign_date") == yesterday:
        record["streak"] += 1
    else:
        record["streak"] = 1  # 断签或首次，归 1

    # 更新数据
    record["total_days"] += 1
    record["last_sign_date"] = today
    record["today_sign_at"] = datetime.now().isoformat(timespec="seconds")

    save_sign_in_data(data)

    # 计算排名
    rank = calculate_ranking(group_id, user_id, data)

    # 计算今日第几位签到者
    today_order = calculate_today_order(group_id, user_id, data)

    return {
        "success": True,
        "today_order": today_order,
        "total_days": record["total_days"],
        "streak": record["streak"],
        "rank": rank,
    }


def calculate_ranking(group_id: str, user_id: str, data: Dict) -> int:
    """
    计算签到排名。
    排序规则: total_days 降序 → streak 降序 → today_sign_at 升序（早签到的排前）

    返回:
        排名（从 1 开始）
    """
    group_data = data.get(group_id, {})
    if not group_data:
        return 1

    users = []
    for uid, rec in group_data.items():
        if uid == user_id:
            users.append((uid, rec, True))
        else:
            users.append((uid, rec, False))

    # 排序: total_days 降序(-), streak 降序(-), today_sign_at 升序(早→前)
    # 对于没有 today_sign_at 的用户（比如昨天签到但今天没签），用 last_sign_date 做 tiebreaker
    def sort_key(item):
        uid, rec, is_self = item
        total = rec.get("total_days", 0)
        streak = rec.get("streak", 0)
        sign_at = rec.get("today_sign_at", rec.get("last_sign_date", "9999"))
        return (-total, -streak, sign_at)

    users.sort(key=sort_key)

    for i, (uid, _, is_self) in enumerate(users, 1):
        if is_self:
            return i

    return 1  # 不应该走到这里


def calculate_today_order(group_id: str, user_id: str, data: Dict) -> int:
    """
    计算今日第几位签到者。
    统计当天 last_sign_date == 今日 且 today_sign_at <= 当前用户的时间的用户数。

    返回:
        今日签到序号（从 1 开始）
    """
    today = date.today().isoformat()
    group_data = data.get(group_id, {})
    target_sign_at = group_data.get(user_id, {}).get("today_sign_at", "")

    count = 0
    for uid, rec in group_data.items():
        if rec.get("last_sign_date") == today:
            sign_at = rec.get("today_sign_at", "")
            if sign_at <= target_sign_at:
                count += 1

    return count