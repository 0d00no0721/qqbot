#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 核心引擎 — 消息监听、指标统计、定价算法。

每收到一条群消息 → on_message() 更新滚动窗口指标。
每 10 分钟 → refresh_prices() 基于窗口数据 + 各股票定价公式刷新价格。
"""

import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .data import (
    STOCK_CODES,
    DEFAULT_STOCK_NAMES,
    load_group_config,
    load_prices,
    save_prices,
    init_group_data,
)

# ========== 消息分类 · 关键词词典 ==========

# 人文类关键词（30003A 人文思潮）
KEYWORDS_HUMANITIES = [
    "社会", "历史", "文学", "哲学", "八卦", "情感", "树洞",
    "政治", "经济", "文化", "艺术", "心理", "教育", "伦理",
    "人生", "意义", "价值", "传统", "现代", "后现代",
    "阶级", "平等", "自由", "民主", "权利",
]

# 科技类关键词（30003B 科技前沿）
KEYWORDS_TECH = [
    "代码", "物理", "化学", "电池", "硬件", "数学", "算法",
    "AI", "人工智能", "机器学习", "深度学习", "神经网络",
    "编程", "Python", "C++", "Rust", "Java", "Go",
    "前端", "后端", "数据库", "操作系统", "编译器", "开源",
    "芯片", "半导体", "量子", "纳米", "生物", "基因",
    "火箭", "卫星", "航天", "机器人", "自动化",
]

# 战雷关键词（100001 战雷航空）
KEYWORDS_WAR_THUNDER = [
    "战雷", "WT", "陆战", "空战", "金币机", "爬升", "魔法",
    "安东", "蜗牛", "gaijin", "WarThunder", "war thunder",
    "轰炸机", "战斗机", "坦克", "装甲", "弹道", "穿深",
    "权重", "研发", "联队", "全真", "历史模式", "街机",
    "顶喷", "活塞", "电风扇", "喷气", "导弹", "雷达",
]

# 二游关键词（100002 二游娱乐）
KEYWORDS_GACHA = [
    "原神", "鸣潮", "崩铁", "崩坏", "星穹铁道", "绝区零",
    "大月卡", "抽卡", "歪了", "圣遗物", "声骸", "保底",
    "十连", "单抽", "氪金", "648", "首充", "限定池",
    "米哈游", "原批", "周本", "深渊", "忘却之庭",
    "方舟", "明日方舟", "FGO", "碧蓝航线", "碧蓝档案",
    "少女前线", "蔚蓝档案", "nikke", "胜利女神",
    "欧皇", "非酋", "晒卡", "沉船",
]

# 编译正则（大小写不敏感）
RE_HUMANITIES = re.compile("|".join(KEYWORDS_HUMANITIES), re.IGNORECASE)
RE_TECH = re.compile("|".join(KEYWORDS_TECH), re.IGNORECASE)
RE_WAR_THUNDER = re.compile("|".join(KEYWORDS_WAR_THUNDER), re.IGNORECASE)
RE_GACHA = re.compile("|".join(KEYWORDS_GACHA), re.IGNORECASE)

# ========== 滚动窗口数据结构 ==========

class IndicatorWindow:
    """单个群的 10 分钟滚动指标窗口。"""

    def __init__(self, group_id: str, owner_qq: str):
        self.group_id = group_id
        self.owner_qq = str(owner_qq) if owner_qq else ""
        self.reset()

    def reset(self) -> None:
        """清空窗口数据（每次价格刷新后调用）。"""
        self.owner_words: int = 0
        self.total_words: int = 0
        self.image_count: int = 0
        self.short_msg_count: int = 0   # ≤3 字消息数
        self.forward_count: int = 0      # 合并转发消息数
        self.long_text_humanities: int = 0  # 人文类长文本数
        self.long_text_tech: int = 0        # 科技类长文本数
        self.msg_timestamps: List[float] = []  # 消息时间戳（秒）
        self.keyword_war_thunder: int = 0
        self.keyword_gacha: int = 0
        self.bot_command_count: int = 0
        self.total_msgs: int = 0

    def record_message(
        self,
        user_id: str,
        raw_message: str,
        *,
        is_bot_command: bool = False,
    ) -> None:
        """记录一条消息到滚动窗口。"""
        now = time.time()
        self.msg_timestamps.append(now)
        self.total_msgs += 1

        # 统计字数
        msg_len = len(raw_message)
        word_count = _count_chinese_chars(raw_message)

        # 群主发言占比
        if self.owner_qq and str(user_id) == self.owner_qq:
            self.owner_words += word_count
        self.total_words += word_count

        # 图片数（QQ 消息中的 [CQ:image 或 [图片] 等标记）
        image_matches = re.findall(r'\[CQ:image[^\]]*\]|\[图片\]|\[表情\]', raw_message)
        self.image_count += len(image_matches)

        # 短消息（≤3 字，不含图片标记）
        clean_msg = _strip_cq_codes(raw_message)
        if len(clean_msg) <= 3 and clean_msg.strip():
            self.short_msg_count += 1

        # 合并转发消息
        if '[CQ:forward' in raw_message or '合并转发' in raw_message:
            self.forward_count += 1

        # 长文本分类（>50 字）
        if len(clean_msg) > 50:
            if RE_HUMANITIES.search(raw_message):
                self.long_text_humanities += 1
            if RE_TECH.search(raw_message):
                self.long_text_tech += 1

        # 战雷关键词
        wt_matches = RE_WAR_THUNDER.findall(raw_message)
        self.keyword_war_thunder += len(wt_matches)

        # 二游关键词
        ga_matches = RE_GACHA.findall(raw_message)
        self.keyword_gacha += len(ga_matches)

        # 机器人指令
        if is_bot_command:
            self.bot_command_count += 1


def _count_chinese_chars(text: str) -> int:
    """统计中文字符数（含中文标点）。"""
    count = 0
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff00' <= ch <= '\uffef':
            count += 1
        elif ch.isalpha():
            count += 1  # 英文字母也算 1 个"字"
    return count


def _strip_cq_codes(text: str) -> str:
    """移除 CQ 码，返回纯文本。"""
    return re.sub(r'\[CQ:[^\]]*\]', '', text)


# ========== 全局窗口注册表 ==========

_windows: Dict[str, IndicatorWindow] = {}


def _get_or_create_window(group_id: str) -> IndicatorWindow:
    """获取或创建某群的指标窗口。"""
    if group_id not in _windows:
        config = load_group_config(group_id)
        owner_qq = config.get("owner_qq", "")
        _windows[group_id] = IndicatorWindow(group_id, owner_qq)
    return _windows[group_id]


# ========== 公开 API：消息监听 ==========

def on_message(
    group_id: str,
    user_id: str,
    raw_message: str,
    *,
    is_bot_command: bool = False,
) -> None:
    """
    每条群消息调用一次，更新该群的指标窗口。
    此函数应在 reverse_bot.py 的群消息处理循环中调用。

    参数：
        group_id: 群号
        user_id:  发送者 QQ 号
        raw_message: 原始消息文本（含 CQ 码）
        is_bot_command: 此消息是否为机器人指令（用于 900001 智械危机指标）
    """
    window = _get_or_create_window(group_id)
    window.record_message(user_id, raw_message, is_bot_command=is_bot_command)


# ========== 公开 API：价格查询 ==========

def get_price(group_id: str, stock_code: str) -> float:
    """获取某股票当前公允价格 P。"""
    prices = load_prices(group_id)
    current = prices.get("current", {})
    return float(current.get(stock_code, 100.0))


def get_all_prices(group_id: str) -> Dict[str, float]:
    """获取全部股票当前价格。返回 {stock_code: price}。"""
    prices = load_prices(group_id)
    return dict(prices.get("current", {}))


def get_price_history(
    group_id: str,
    stock_code: str,
    hours: int = 1,
) -> List[Dict[str, Any]]:
    """
    获取某股票的历史价格序列。
    返回 [{"timestamp": str, "price": float}, ...]。
    """
    prices = load_prices(group_id)
    history = prices.get("history", {}).get(stock_code, [])
    if hours <= 0:
        return history
    cutoff = time.time() - hours * 3600
    return [h for h in history if _ts_to_unix(h.get("timestamp", "")) >= cutoff]


def get_stock_info(group_id: str, stock_code: str) -> Dict[str, Any]:
    """获取单支股票的完整信息（名称、代码、当前价、历史峰值等）。"""
    prices = load_prices(group_id)
    config = load_group_config(group_id)
    return {
        "code": stock_code,
        "name": DEFAULT_STOCK_NAMES.get(stock_code, stock_code),
        "price": float(prices.get("current", {}).get(stock_code, 100.0)),
        "all_time_high": float(prices.get("all_time_high", {}).get(stock_code, 100.0)),
        "total_shares": config["stocks"].get(stock_code, {}).get("total_shares", 10_000),
        "circuit_breaker": prices.get("circuit_breaker", {}).get(stock_code),
    }


def get_all_stocks(group_id: str) -> List[Dict[str, Any]]:
    """获取全部股票信息列表。"""
    return [get_stock_info(group_id, code) for code in STOCK_CODES]


# ========== 公开 API：价格刷新（定时任务调用） ==========

def refresh_prices(group_id: str) -> Dict[str, float]:
    """
    每 10 分钟调用一次：基于滚动窗口指标计算新价格，更新价格存储。
    返回 {stock_code: new_price}。

    如果该群尚未初始化，会自动调用 init_group_data 创建默认数据。
    """
    # 确保群数据已初始化
    prices = load_prices(group_id)
    if not prices.get("current"):
        init_group_data(group_id)
        prices = load_prices(group_id)

    window = _get_or_create_window(group_id)
    current_prices = prices.get("current", {})
    all_time_high = prices.get("all_time_high", {})
    history = prices.get("history", {})

    new_prices: Dict[str, float] = {}
    now_ts = datetime.now().isoformat()

    # ── 逐支股票计算新价格 ──
    for code in STOCK_CODES:
        old_price = float(current_prices.get(code, 100.0))
        delta = _compute_delta(code, window)
        new_price = old_price * (1.0 + delta)

        # 价格下界
        config = load_group_config(group_id)
        floor = config.get("price_floor", 1.0)
        new_price = max(new_price, floor)

        new_prices[code] = round(new_price, 2)

        # 更新历史序列
        if code not in history:
            history[code] = []
        history[code].append({"timestamp": now_ts, "price": new_price})

        # 清理超过 24 小时的历史（节省存储）
        history[code] = _trim_history(history[code], max_hours=24)

        # 更新历史峰值
        prev_high = float(all_time_high.get(code, 0.0))
        if new_price > prev_high:
            all_time_high[code] = new_price

    # ── 写入存储 ──
    prices["current"] = new_prices
    prices["history"] = history
    prices["all_time_high"] = all_time_high
    save_prices(group_id, prices)

    # ── 重置窗口（新的一轮 10 分钟统计开始） ──
    window.reset()

    return new_prices


# ========== 核心定价函数 ==========

def _compute_delta(code: str, window: IndicatorWindow) -> float:
    """计算某支股票的变动率 Δ ∈ [-0.15, +0.15]。"""
    if code == "600001":
        return _delta_owner_control(window)
    elif code == "300001":
        return _delta_watering_estate(window)
    elif code == "300002":
        return _delta_forward_logistics(window)
    elif code == "30003A":
        return _delta_thinker_humanities(window)
    elif code == "30003B":
        return _delta_thinker_tech(window)
    elif code == "000001":
        return _delta_density_momentum(window)
    elif code == "100001":
        return _delta_war_thunder(window)
    elif code == "100002":
        return _delta_gacha(window)
    elif code == "900001":
        return _delta_robot_crisis(window)
    return 0.0


def _clamp_delta(delta: float) -> float:
    """将 Δ 限制在 ±15%。"""
    return max(-0.15, min(0.15, delta))


# ── 600001 群主控股 ──
def _delta_owner_control(window: IndicatorWindow) -> float:
    if window.total_words == 0:
        return -0.01  # 无人发言，微跌
    p_owner = window.owner_words / window.total_words

    if 0.05 <= p_owner <= 0.15:
        delta = 0.02  # 健康区间，稳步上涨
    elif p_owner < 0.01:
        delta = -0.03  # 群龙无首，阴跌
    elif p_owner > 0.30:
        delta = -0.08  # 极权忧虑，大跌
    elif p_owner < 0.05:
        # 0.01 → 0.05 线性：从 -0.03 到 +0.02
        delta = -0.03 + (p_owner - 0.01) / 0.04 * 0.05
    else:  # 0.15 → 0.30
        # 线性：从 +0.02 到 -0.08
        delta = 0.02 - (p_owner - 0.15) / 0.15 * 0.10

    return _clamp_delta(delta)


# ── 300001 水群地产 ──
def _delta_watering_estate(window: IndicatorWindow) -> float:
    if window.total_msgs == 0:
        return -0.03
    index = (window.image_count + window.short_msg_count) / window.total_msgs
    delta = (index - 0.3) * 0.2
    return _clamp_delta(delta)


# ── 300002 搬运物流 ──
def _delta_forward_logistics(window: IndicatorWindow) -> float:
    delta = window.forward_count * 0.03
    return _clamp_delta(min(delta, 0.15))


# ── 30003A 人文思潮（子母股零和竞争） ──
def _delta_thinker_humanities(window: IndicatorWindow) -> float:
    delta_a_raw = window.long_text_humanities * 0.04
    delta_b_raw = window.long_text_tech * 0.04
    delta = delta_a_raw - delta_b_raw  # 零和：人文相对科技的优势
    return _clamp_delta(delta)


# ── 30003B 科技前沿（子母股零和竞争） ──
def _delta_thinker_tech(window: IndicatorWindow) -> float:
    delta_a_raw = window.long_text_humanities * 0.04
    delta_b_raw = window.long_text_tech * 0.04
    delta = delta_b_raw - delta_a_raw  # 零和：科技相对人文的优势
    return _clamp_delta(delta)


# ── 000001 消息密度 ──
def _delta_density_momentum(window: IndicatorWindow) -> float:
    if len(window.msg_timestamps) < 2:
        return -0.05  # 极少消息，冷场
    # 计算窗口的 TPM（每分钟消息数）
    oldest = min(window.msg_timestamps)
    newest = max(window.msg_timestamps)
    span = newest - oldest
    if span <= 0:
        return -0.05
    tpm = len(window.msg_timestamps) / (span / 60.0)

    if tpm > 50:
        delta = 0.10
    elif tpm < 1:
        delta = -0.10
    else:
        # 1 → 50 之间线性插值：从 -0.10 到 +0.10
        delta = -0.10 + (tpm - 1) / 49.0 * 0.20

    return _clamp_delta(delta)


# ── 100001 战雷航空 ──
def _delta_war_thunder(window: IndicatorWindow) -> float:
    if window.total_msgs == 0:
        return -0.02
    density = window.keyword_war_thunder / window.total_msgs
    delta = density * 0.30
    return _clamp_delta(delta)


# ── 100002 二游娱乐 ──
def _delta_gacha(window: IndicatorWindow) -> float:
    if window.total_msgs == 0:
        return -0.02
    density = window.keyword_gacha / window.total_msgs
    delta = density * 0.30
    return _clamp_delta(delta)


# ── 900001 智械危机 ──
def _delta_robot_crisis(window: IndicatorWindow) -> float:
    delta = window.bot_command_count * 0.02
    return _clamp_delta(min(delta, 0.15))


# ========== 历史维护 ==========

def _ts_to_unix(ts: str) -> float:
    """将 ISO 时间戳转为 Unix 秒，失败返回 0。"""
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return 0.0


def _trim_history(history: List[Dict], max_hours: int = 24) -> List[Dict]:
    """只保留最近 max_hours 的价格历史。"""
    if not history:
        return history
    cutoff = time.time() - max_hours * 3600
    trimmed = [h for h in history if _ts_to_unix(h.get("timestamp", "")) >= cutoff]
    # 始终保留最后一条（即使超时），用于熔断计算等
    if not trimmed and history:
        trimmed = [history[-1]]
    return trimmed