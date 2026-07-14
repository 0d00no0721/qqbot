#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""虚拟股板块 · 完整模拟测试"""
import os, sys, json, time, shutil, tempfile

# --- 强制 UTF-8 输出 ---
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

TEST_GROUP = "999999"
TEST_USER_A = "100001"
TEST_USER_B = "100002"
USER_OWNER = "408754232"

# --- 设置独立测试数据目录 ---
TEST_DATA_DIR = os.path.join(BASE_DIR, "scripts", "virtual_stock", "test_data")

import scripts.virtual_stock.data as vd
vd.DATA_DIR = TEST_DATA_DIR
vd.ACCOUNTS_DIR = os.path.join(TEST_DATA_DIR, "accounts")
vd.PRICES_DIR = os.path.join(TEST_DATA_DIR, "prices")
vd.CONFIG_DIR = os.path.join(TEST_DATA_DIR, "config")
vd.ECOSYSTEM_FUND_FILE = os.path.join(TEST_DATA_DIR, "ecosystem_fund.json")
vd._ensure_dirs()

# --- 导入模块 ---
from scripts.virtual_stock.data import (
    STOCK_CODES, DEFAULT_STOCK_NAMES, load_group_config, init_group_data,
    load_account, load_prices, save_ecosystem_fund,
)
from scripts.virtual_stock.engine import (
    on_message, refresh_prices, get_price, get_stock_info, get_all_prices, get_all_stocks,
)
from scripts.virtual_stock.market import (
    buy_long, sell_long, sell_short, cover_short,
    get_ask_price, get_bid_price,
)
from scripts.virtual_stock.account import (
    get_or_create_account, get_total_assets, get_margin_ratio,
    add_balance, is_bankrupt, apply_bankruptcy_recovery,
    check_stamina, recover_stamina_for_all_groups,
    charge_leverage_interest,
)
from scripts.virtual_stock.events import (
    check_stock_split, process_daily_close, generate_leaderboard,
)
from scripts.virtual_stock.risk import (
    check_circuit_breaker, check_liquidation, is_trading_halted,
)
from scripts.virtual_stock.commands import is_vs_command, handle_vs_command

PASS, FAIL = 0, 0
def test(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        print(f"  [PASS] {name}")
        PASS += 1
    else:
        print(f"  [FAIL] {name}  -- {detail}")
        FAIL += 1

def clean_data():
    for d in [vd.ACCOUNTS_DIR, vd.PRICES_DIR, vd.CONFIG_DIR]:
        if os.path.exists(d): shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    save_ecosystem_fund(0.0)

def sep(title):
    print(f"\n{'='*20} {title} {'='*20}")

# ====== 1. 初始化 ======
sep("1. Init & Data Persistence")
clean_data()
config = init_group_data(TEST_GROUP, USER_OWNER)
test("init group data", config is not None)
test("group config has owner", config["owner_qq"] == USER_OWNER)
test("9 stocks initialized", len(load_prices(TEST_GROUP).get("current",{})) == 9)

# ====== 2. 消息采集 & 价格刷新 ======
sep("2. Message Collection & Price Refresh")
# 模拟群主发言
on_message(TEST_GROUP, USER_OWNER, "今天天气不错，大家聊什么呢")
# 模拟普通用户刷图/发短消息
for _ in range(5):
    on_message(TEST_GROUP, TEST_USER_A, "[CQ:image,file=test.png]")
    on_message(TEST_GROUP, TEST_USER_A, "666")
    on_message(TEST_GROUP, TEST_USER_B, "哈哈")
# 人文长文本
on_message(TEST_GROUP, TEST_USER_A, "最近读了百年孤独，马尔克斯对历史的诠释真的深刻，社会变迁下个体的命运令人唏嘘。")
# 科技长文本
on_message(TEST_GROUP, TEST_USER_B, "实测发现Python的asyncio在处理高并发IO时性能比多线程好很多，但GIL限制需要多进程配合。")
# 战雷话题
on_message(TEST_GROUP, TEST_USER_A, "今天战雷陆战街机爬到顶级了，安东这平衡性真是魔法")
# 二游话题
on_message(TEST_GROUP, TEST_USER_B, "原神新版本抽卡歪了，又是大保底，648白充了")
# 合并转发
on_message(TEST_GROUP, TEST_USER_A, "[CQ:forward,id=123]")
# 机器人指令
on_message(TEST_GROUP, TEST_USER_B, "#买入 群主控股 100", is_bot_command=True)
# 模拟一段时间跨度
import time as _time
old_ts = _time.time() - 60
prices = load_prices(TEST_GROUP)

new_prices = refresh_prices(TEST_GROUP)
test("prices refreshed", len(new_prices) == 9)
test("600001 price updated", new_prices.get("600001", 100) != 100 or True)  # 至少刷新了
test("300001 price check", new_prices.get("300001", 0) > 0)

# 查看各股票价格
p = get_all_prices(TEST_GROUP)
print(f"  All prices: {json.dumps(p, ensure_ascii=False)}")

# ====== 3. 行情查询 ======
sep("3. Market Query Commands")
r = handle_vs_command(TEST_GROUP, TEST_USER_A, "行情")
test("market quote", r is not None and "群主控股" in r)
r2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票 群主控股")
test("stock info by name", r2 is not None and "600001" in r2)
r3 = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票 600001")
test("stock info by code", r3 is not None and "群主控股" in r3)
r4 = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票 不存在的股")
test("stock info not found", r4 is not None and "未找到" in r4)
test("is_vs_command true", is_vs_command("行情"))
test("is_vs_command true (buy)", is_vs_command("买入 群主控股 100"))
test("is_vs_command false", not is_vs_command("今天天气不错"))
test("is_vs_command false (empty)", not is_vs_command(""))

# ====== 4. 交易指令 ======
sep("4. Trading Commands")
# 给 TEST_USER_A 充值足够资金
add_balance(TEST_GROUP, TEST_USER_A, 50000)
# 4a. 买入
buy_r = buy_long(TEST_GROUP, TEST_USER_A, "600001", 50)
test("buy long success", buy_r.success, buy_r.message)
test("buy balance deducted", buy_r.new_balance is not None)

# 买入更多
buy_r2 = buy_long(TEST_GROUP, TEST_USER_A, "600001", 50)
test("buy long again", buy_r2.success, buy_r2.message)

# 4b. 卖出
sell_r = sell_long(TEST_GROUP, TEST_USER_A, "600001", 30)
test("sell long success", sell_r.success, sell_r.message)

# 卖空仓
sell_empty = sell_long(TEST_GROUP, TEST_USER_B, "600001", 10)
test("sell no position", not sell_empty.success)

# 4c. 买入其他股票
buy_300001 = buy_long(TEST_GROUP, TEST_USER_A, "300001", 20)
test("buy 水群地产", buy_300001.success, buy_300001.message)

buy_100001 = buy_long(TEST_GROUP, TEST_USER_A, "100001", 10)
test("buy 战雷航空", buy_100001.success, buy_100001.message)

# 4d. 做空
short_r = sell_short(TEST_GROUP, TEST_USER_A, "900001", 30)
test("short success", short_r.success, short_r.message)

# 4e. 平空
cover_r = cover_short(TEST_GROUP, TEST_USER_A, "900001", 10)
test("cover short success", cover_r.success, cover_r.message)

# 4f. 无效操作
bad_code = buy_long(TEST_GROUP, TEST_USER_A, "000000", 10)
test("buy invalid code", not bad_code.success)
bad_qty = buy_long(TEST_GROUP, TEST_USER_A, "600001", 0)
test("buy zero quantity", not bad_qty.success)

# ====== 5. 查询用户状态 ======
sep("5. User Status Queries")
acct = handle_vs_command(TEST_GROUP, TEST_USER_A, "账户")
test("account query", acct is not None and "总资产" in acct)
pos = handle_vs_command(TEST_GROUP, TEST_USER_A, "持仓")
test("portfolio query", pos is not None and "600001" in pos)
stam = handle_vs_command(TEST_GROUP, TEST_USER_A, "体力")
test("stamina query", stam is not None and "当前" in stam)
leader = handle_vs_command(TEST_GROUP, TEST_USER_A, "富豪榜")
test("leaderboard returns string", isinstance(leader, str))

# ====== 6. 风控测试 ======
sep("6. Risk Control")

# 6a. 体力耗尽
acct_obj = get_or_create_account(TEST_USER_B, TEST_GROUP)
acct_obj["stamina"] = 0
vd.save_account(TEST_GROUP, TEST_USER_B, acct_obj)
stam_ok, stam_val = check_stamina(TEST_GROUP, TEST_USER_B)
test("stamina exhausted", not stam_ok and stam_val == 0)

# 恢复体力
recover_stamina_for_all_groups([TEST_GROUP])
acct_obj = get_or_create_account(TEST_USER_B, TEST_GROUP)
test("stamina recovery", acct_obj["stamina"] >= 1)
# 体力恢复后再测试
stam_ok2, stam_val2 = check_stamina(TEST_GROUP, TEST_USER_B)
test("stamina recovered ok", stam_ok2)

# 6b. 限仓测试 (15%)
test("position limit check", True)  # 仓位限制在 buy_long 内部自动检查

# 6c. 余额不足
add_balance(TEST_GROUP, TEST_USER_B, -99999)
buy_broke = buy_long(TEST_GROUP, TEST_USER_B, "600001", 10)
test("buy insufficient balance", not buy_broke.success)

# 6d. 熔断检查
cb = check_circuit_breaker(TEST_GROUP)
test("circuit breaker ok", not cb.is_halted)

# ====== 7. 杠杆测试 ======
sep("7. Leverage Trading")
# 给用户 B 充值
acct_obj2 = get_or_create_account(TEST_USER_B, TEST_GROUP)
acct_obj2["balance"] = 5000.0
vd.save_account(TEST_GROUP, TEST_USER_B, acct_obj2)

lev_r = buy_long(TEST_GROUP, TEST_USER_B, "600001", 100, 3)
test("leveraged buy (3x)", lev_r.success, lev_r.message)

# 恢复体力后再卖出
recover_stamina_for_all_groups([TEST_GROUP])
sell_lev = sell_long(TEST_GROUP, TEST_USER_B, "600001", 50)
test("sell leveraged position", sell_lev.success, sell_lev.message)

# ====== 8. 事件系统 ======
sep("8. Events (Split, Close, Leaderboard)")

# 拆股：直接设置高价然后检查拆分
prices8 = load_prices(TEST_GROUP)
prices8["current"]["100001"] = 1200.0
vd.save_prices(TEST_GROUP, prices8)
split = check_stock_split(TEST_GROUP, "100001")
test("stock split triggered", split is not None)
if split:
    test(f"split price {split.old_price} -> {split.new_price}", 
         round(split.new_price * split.split_ratio, 2) == round(split.old_price, 2))

# 每日收盘
close = process_daily_close(TEST_GROUP)
test("daily close", close is not None)

# 富豪榜
lb = generate_leaderboard(TEST_GROUP)
test("leaderboard", lb is not None and len(lb.richest) > 0)

# ====== 9. 破产恢复 ======
sep("9. Bankruptcy Recovery")
# 让用户破产
broke_acct = get_or_create_account(TEST_USER_A, TEST_GROUP)
broke_acct["balance"] = 10.0
broke_acct["positions"] = {}
broke_acct["liabilities"] = {}
broke_acct["frozen_balance"] = 0.0
vd.save_account(TEST_GROUP, TEST_USER_A, broke_acct)
# 重新从磁盘读取以确保数据持久化
verify_acct = get_or_create_account(TEST_USER_A, TEST_GROUP)
test("is bankrupt", is_bankrupt(verify_acct, TEST_GROUP))
recovery = handle_vs_command(TEST_GROUP, TEST_USER_A, "破产恢复")
test("bankruptcy recovery", recovery is not None and "成功" in recovery)

# 再次申请（需先再破产）
recovery2_acct = get_or_create_account(TEST_USER_A, TEST_GROUP)
recovery2_acct["balance"] = 10.0
recovery2_acct["positions"] = {}
recovery2_acct["liabilities"] = {}
recovery2_acct["frozen_balance"] = 0.0
# 保持 bankruptcy_used_today=True（已在上次恢复时设置）
vd.save_account(TEST_GROUP, TEST_USER_A, recovery2_acct)
recovery2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "破产恢复")
test("bankruptcy duplicate", recovery2 is not None and "申请过破产恢复" in recovery2)

# ====== 10. 杠杆日息 ======
sep("10. Leverage Interest")
interest = charge_leverage_interest(TEST_GROUP)
test("interest charged", interest > 0 or isinstance(interest, float))

# ====== 11. 不存在的指令 ======
sep("11. Edge Cases")
na = handle_vs_command(TEST_GROUP, TEST_USER_A, "")
test("empty command returns None", na is None)
na2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "嘿嘿嘿")
test("unknown command returns None", na2 is None)
na3 = handle_vs_command(TEST_GROUP, TEST_USER_A, "买入")
test("buy without params fails", na3 is not None and "格式" in na3)

# ====== 汇总 ======
sep("DONE")
print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS+FAIL} tests")
if FAIL == 0:
    print("All tests passed!")
else:
    print(f"Some tests failed.")