#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股 · 持久化层 — 所有 JSON 数据的读写、初始化、原子写入。
数据目录：scripts/virtual_stock/data/
"""

import json
import os
import shutil
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Generator, Optional

# ========== 路径常量 ==========
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(MODULE_DIR, "data")
ACCOUNTS_DIR = os.path.join(DATA_DIR, "accounts")
PRICES_DIR = os.path.join(DATA_DIR, "prices")
CONFIG_DIR = os.path.join(DATA_DIR, "config")
ECOSYSTEM_FUND_FILE = os.path.join(DATA_DIR, "ecosystem_fund.json")

# ========== 默认配置模板 ==========
DEFAULT_STOCK_NAMES = {
    "600001": "群主控股",
    "300001": "水群地产",
    "300002": "搬运物流",
    "30003A": "人文思潮",
    "30003B": "科技前沿",
    "000001": "消息密度",
    "100001": "战雷航空",
    "100002": "二游娱乐",
    "900001": "智械危机",
}

STOCK_CODES = list(DEFAULT_STOCK_NAMES.keys())

# ========== 硬编码常量 ==========

# 群主 QQ 号（600001 群主控股的核心指标依赖此值）
OWNER_QQ = "408754232"

# ========== 默认配置模板 ==========

DEFAULT_GROUP_CONFIG = {
    "owner_qq": OWNER_QQ,
    "stocks": {
        code: {"total_shares": 10_000, "initial_price": 100.0}
        for code in STOCK_CODES
    },
    "dividend_rate": 0.0005,       # 每周分红比例（对标 A 股股息率）
    "signin_bonus_rate": 0.001,     # 签到奖励比例（总资产的 1‰）
    "leverage_max": 3,              # 最大杠杆倍数
    "leverage_interest_rate": 0.002,  # 杠杆日息 0.2%
    "stamina_max": 10,              # 体力值上限
    "stamina_recover_interval": 1800,  # 体力恢复间隔（秒）
    "position_limit_ratio": 0.15,   # 单股持仓上限比例
    "initial_balance": 1000.0,      # 新账户初始金币
    "bankruptcy_threshold": 50.0,   # 破产线
    "bankruptcy_recovery": 200.0,   # 破产恢复金
    "circuit_breaker_single": 0.30,  # 单股熔断阈值
    "circuit_breaker_market": 0.15,  # 大盘熔断阈值
    "circuit_breaker_hours": 1,      # 熔断持续小时
    "split_threshold": 1000.0,       # 拆股触发价
    "split_ratio": 10,               # 拆股比例
    "price_floor": 1.0,              # 股价下界
    "refresh_interval": 600,         # 价格刷新间隔（秒）= 10 分钟
}


# ========== 工具函数 ==========

# ----- 账户读写锁（防止调度器协程间读-改-写竞态） -----

_account_locks: Dict[str, threading.RLock] = {}
_account_locks_guard = threading.Lock()


def _get_account_lock(group_id: str) -> threading.RLock:
    """获取某群的账户读写锁（RLock 允许同一线程重入）。"""
    with _account_locks_guard:
        if group_id not in _account_locks:
            _account_locks[group_id] = threading.RLock()
        return _account_locks[group_id]


@contextmanager
def locked_accounts(group_id: str) -> Generator[Dict[str, Any], None, None]:
    """
    账户读-改-写上下文管理器：加锁 → 加载 → yield → 自动保存 → 解锁。
    确保 scheduler 的多个协程不会交叉读写同一群的 accounts.json。

    用法：
        with locked_accounts(group_id) as accounts:
            accounts[uid]["balance"] += 100
        # 退出时自动 save_accounts
    """
    lock = _get_account_lock(group_id)
    with lock:
        accounts = load_accounts(group_id)
        yield accounts
        save_accounts(group_id, accounts)


def _ensure_dirs() -> None:
    """确保所有数据子目录存在。"""
    for d in (DATA_DIR, ACCOUNTS_DIR, PRICES_DIR, CONFIG_DIR):
        os.makedirs(d, exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    """加载 JSON 文件，不存在或损坏时返回空 {}。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[虚拟股·data] 读取 {path} 失败: {e}，回退为空字典")
        # 备份损坏文件
        damaged = path + f".damaged.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            shutil.copy2(path, damaged)
        except Exception:
            pass
        return {}


def save_json(path: str, data: Any, atomic: bool = True) -> None:
    """保存 JSON 文件。默认原子写入（临时文件 → 重命名），防止写入中途崩溃导致文件损坏。"""
    _ensure_dirs()
    if atomic:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json.tmp",
            prefix="vs_",
            dir=os.path.dirname(path),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp_path, path)
        except Exception:
            os.unlink(tmp_path)
            raise
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def path_for_group(base_dir: str, group_id: str) -> str:
    """生成群隔离的文件路径。"""
    # group_id 转为纯数字文件名，防止路径注入
    safe_gid = str(int(group_id))
    return os.path.join(base_dir, f"{safe_gid}.json")


# ========== 账户持久化 ==========

def load_accounts(group_id: str) -> Dict[str, Any]:
    """加载某群的所有用户账户。返回 {user_id: account_dict}。"""
    path = path_for_group(ACCOUNTS_DIR, group_id)
    return load_json(path)


def save_accounts(group_id: str, accounts: Dict[str, Any]) -> None:
    """保存某群的所有用户账户。"""
    path = path_for_group(ACCOUNTS_DIR, group_id)
    save_json(path, accounts)


def load_account(group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """加载单个用户的账户，不存在返回 None。"""
    accounts = load_accounts(group_id)
    return accounts.get(user_id)


def save_account(group_id: str, user_id: str, account: Dict[str, Any]) -> None:
    """保存单个用户的账户（读-改-写模式，加锁防止并发竞态）。"""
    with locked_accounts(group_id) as accounts:
        accounts[user_id] = account


# ========== 价格持久化 ==========

def load_prices(group_id: str) -> Dict[str, Any]:
    """加载某群的价格数据（当前价格 + 历史序列 + 熔断状态）。"""
    path = path_for_group(PRICES_DIR, group_id)
    return load_json(path)


def save_prices(group_id: str, price_data: Dict[str, Any]) -> None:
    """保存某群的价格数据。"""
    path = path_for_group(PRICES_DIR, group_id)
    save_json(path, price_data)


# ========== 配置持久化 ==========

def load_group_config(group_id: str) -> Dict[str, Any]:
    """加载某群的虚拟股配置。首次加载时自动从默认模板创建。"""
    path = path_for_group(CONFIG_DIR, group_id)
    config = load_json(path)
    if not config:
        # 首次初始化：从默认模板创建
        config = _deep_copy_default_config()
        save_json(path, config)
    else:
        # 合并缺失字段（兼容旧版本配置）
        merged = _deep_copy_default_config()
        _deep_merge(merged, config)
        config = merged
    return config


def save_group_config(group_id: str, config: Dict[str, Any]) -> None:
    """保存某群的虚拟股配置。"""
    path = path_for_group(CONFIG_DIR, group_id)
    save_json(path, config)


# ========== 生态基金持久化 ==========

def load_ecosystem_fund() -> float:
    """加载生态发展基金余额。"""
    data = load_json(ECOSYSTEM_FUND_FILE)
    return float(data.get("balance", 0.0))


def save_ecosystem_fund(balance: float) -> None:
    """保存生态发展基金余额。"""
    save_json(ECOSYSTEM_FUND_FILE, {
        "balance": round(balance, 2),
        "updated_at": datetime.now().isoformat(),
    })


# ========== 初始化 ==========

def init_group_data(group_id: str, owner_qq: str = "") -> Dict[str, Any]:
    """
    首次启动时为某群创建全套默认数据。
    返回该群的配置 dict。
    """
    _ensure_dirs()

    # 1. 配置
    config = _deep_copy_default_config()
    if owner_qq:
        config["owner_qq"] = str(owner_qq)
    save_group_config(group_id, config)

    # 2. 价格（空白，首次 refresh_prices 时填充）
    initial_prices = _make_initial_price_data(config)
    save_prices(group_id, initial_prices)

    # 3. 账户（空，用户首次交易时创建）
    save_accounts(group_id, {})

    print(f"[虚拟股·data] 群 {group_id} 初始化完成")
    return config


def _make_initial_price_data(config: Dict[str, Any]) -> Dict[str, Any]:
    """根据配置生成初始价格数据结构。"""
    current = {}
    history = {}
    all_time_high = {}
    for code in STOCK_CODES:
        p = config["stocks"][code]["initial_price"]
        current[code] = p
        history[code] = []
        all_time_high[code] = p

    return {
        "current": current,
        "history": history,
        "all_time_high": all_time_high,
        "circuit_breaker": {code: None for code in STOCK_CODES},
    }


def _deep_copy_default_config() -> Dict[str, Any]:
    """深拷贝默认配置模板。"""
    return json.loads(json.dumps(DEFAULT_GROUP_CONFIG))


def _deep_merge(base: Dict, override: Dict) -> None:
    """将 override 递归合并到 base（原地修改）。"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ========== 启动自检 ==========

def ensure_data_integrity() -> None:
    """确保数据目录结构完整。模块导入时自动调用。"""
    _ensure_dirs()
    # 确保生态基金文件存在
    if not os.path.exists(ECOSYSTEM_FUND_FILE):
        save_ecosystem_fund(0.0)


# 模块导入时自动执行
ensure_data_integrity()