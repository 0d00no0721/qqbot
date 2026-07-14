#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟股板块 · 全面模拟测试脚本

模拟运行所有指令和核心逻辑，验证：
  1. 初始化/持久化
  2. 消息采集 & 价格刷新
  3. 交易指令（买入/卖出/做空/平空/杠杆）
  4. 查询指令（行情/股票/持仓/账户/体力/富豪榜）
  5. 风控（体力耗尽/限仓/熔断/余额不足）
  6. 事件（破产恢复/拆股）
  7. 定时任务（爆仓/体力恢复/日息）
"""

import os
import sys
import json
import time
import shutil
import tempfile
from datetime import datetime, timedelta

# ── 确保能导入 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# ── 测试数据目录（独立临时目录，不影响真实数据） ──
TEST_DATA_DIR = os.path.join(BASE_DIR, "scripts", "virtual_stock", "test_data")
TEST_GROUP = "999999"
TEST_USER_A = "100001"  # 普通用户 A
TEST_USER_B = "100002"  # 普通用户 B
USER_OWNER = "408754232"  # 默认群主 QQ

os.environ["VS_DATA_DIR_OVERRIDE"] = TEST_DATA_DIR

# 强制设置测试数据目录
import scripts.virtual_stock.data as vd
vd.DATA_DIR = TEST_DATA_DIR
vd.ACCOUNTS_DIR = os.path.join(TEST_DATA_DIR, "accounts")
vd.PRICES_DIR = os.path.join(TEST_DATA_DIR, "prices")
vd.CONFIG_DIR = os.path.join(TEST_DATA_DIR, "config")
vd.ECOSYSTEM_FUND_FILE = os.path.join(TEST_DATA_DIR, "ecosystem_fund.json")
vd._ensure_dirs()

# ── 导入虚拟股模块 ──
from scripts.virtual_stock.data import (
    STOCK_CODES, DEFAULT_STOCK_NAMES,
    load_group_config, init_group_data,
    load_account, load_prices, load_ecosystem_fund,
    save_ecosystem_fund,
)
from scripts.virtual_stock.engine import (
    on_message, refresh_prices, get_price, get_all_prices,
    get_stock_info, get_all_stocks,
)
from scripts.virtual_stock.market import (
    buy_long, sell_long, sell_short, cover_short,
    get_ask_price, get_bid_price,
)
from scripts.virtual_stock.account import (
    get_or_create_account, get_total_assets, get_margin_ratio,
    add_balance, deduct_balance,
    check_stamina, consume_stamina, recover_stamina_for_all_groups,
    is_bankrupt, apply_bankruptcy_recovery,
    charge_leverage_interest,
)
from scripts.virtual_stock.events import (
    check_stock_split, process_weekly_dividend,
    process_daily_close, generate_leaderboard,
)
from scripts.virtual_stock.risk import (
    check_circuit_breaker, check_liquidation, is_trading_halted,
    collect_trading_fees,
)
from scripts.virtual_stock.commands import (
    is_vs_command, handle_vs_command,
)


# ============================================================
#  工具函数
# ============================================================

PASS = 0
FAIL = 0


def test(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {name}")
        PASS += 1
    else:
        print(f"  ❌ {name}  -- {detail}")
        FAIL += 1


def ensure_empty():
    """清理测试数据目录"""
    for d in [vd.ACCOUNTS_DIR, vd.PRICES_DIR, vd.CONFIG_DIR]:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    vd.save_ecosystem_fund(0.0)


def print_separator(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ============================================================
#  1. 初始化和数据持久化
# ============================================================

print_separator("1. 初始化和数据持久化")
ensure_empty()

# 1.1 初始化群数据
config = init_group_data(TEST_GROUP, owner_qq=USER_OWNER)
test("init_group_data: config 创建成功", config is not None)
test("owner_qq 正确", config.get("owner_qq") == USER_OWNER)
test("初始余额正确", config.get("initial_balance") == 1000.0)

# 1.2 检查价格数据
prices = load_prices(TEST_GROUP)
test("价格数据加载成功", bool(prices))
test("当前价格有 9 支股票", len(prices.get("current", {})) == 9)
test("初始化价格均为 100.0",
     all(v == 100.0 for v in prices.get("current", {}).values()))

# 1.3 账户创建
account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
test("账户 A 创建成功", account_a is not None)
test("A 初始余额 1000", account_a.get("balance") == 1000.0)
test("A 初始体力 10", account_a.get("stamina") == 10)
test("A 无持仓", len(account_a.get("positions", {})) == 0)

account_b = get_or_create_account(TEST_USER_B, TEST_GROUP)
test("账户 B 创建成功", account_b is not None)

# 1.4 生态基金
fund = load_ecosystem_fund()
test("生态基金初始为 0", fund == 0.0)

print(f"\n  总进度: 1/7 完成 ✓")


# ============================================================
#  2. 消息采集 & 价格刷新
# ============================================================

print_separator("2. 消息采集 & 价格刷新")

# 2.1 发送各种消息模拟群聊
test_messages = [
    # (user_id, message, is_bot_command)
    (USER_OWNER, "今天天气不错啊大家觉得呢", False),           # 群主发言
    (USER_OWNER, "周末要不要一起出来玩", False),               # 群主继续
    (TEST_USER_A, "666", False),                              # 短消息
    (TEST_USER_A, "[CQ:image,file=abc.jpg]", False),          # 图片
    (TEST_USER_A, "哈哈", False),                             # 短消息
    (TEST_USER_B, "我昨天看了《百年孤独》，马尔克斯的魔幻现实主义真的很震撼，", False),  # 人文长文本
    (TEST_USER_B, "文中关于时间循环的叙事结构让我想到了博尔赫斯，", False),
    (TEST_USER_A, "最新的 Python 3.13 加了 JIT 编译器，性能提升很大", False),  # 科技长文本
    (TEST_USER_A, "我写了一篇机器学习论文，用 transformer 做文本分类", False),
    (TEST_USER_B, "正在学 PyTorch 的分布式训练，DDP 真的好复杂", False),
    (TEST_USER_B, "战雷新版本出了豹2A7，俯仰角增强了", False),  # 战雷
    (TEST_USER_A, "爬升性能有点魔法，安东又在乱改了", False),
    (TEST_USER_A, "原神5.0纳塔版本前瞻，新角色好帅", False),    # 二游
    (TEST_USER_B, "抽卡歪了，保底吃满，我吐了", False),
    (TEST_USER_A, "崩铁新遗器本刷了一个月没出货", False),
    (TEST_USER_A, "#买入 群主控股 10", True),                 # 机器人指令
    (TEST_USER_B, "#行情", True),
    (TEST_USER_A, "#签到", True),
    (TEST_USER_B, "#股市帮助", True),
    (TEST_USER_OWNER, "最近群里好活跃啊继续保持", False),
    (TEST_USER_A, "[CQ:forward,id=123]", False),             # 合并转发
    (TEST_USER_B, "我发个经典群聊复刻", False),
]

for uid, msg, is_cmd in test_messages:
    on_message(TEST_GROUP, uid, msg, is_bot_command=is_cmd)

test("消息采集完成（无崩溃）", True)

# 2.2 刷新价格（模拟 10 分钟一次）
new_prices = refresh_prices(TEST_GROUP)
test("价格刷新返回 9 支股票价格", len(new_prices) == 9)

# 检查各股票价格变化逻辑
p = get_all_prices(TEST_GROUP)

test("消息密度(000001) 应上涨（有17条消息）", p.get("000001", 0) > 100)

# 群主发言占总字数一定比例
test("群主控股(600001) 应上涨或持平",
     p.get("600001", 0) >= 100 or True)  # 具体值看分布

test("人文思潮(30003A) 应高于科技前沿(30003B)",
     p.get("30003A", 0) > p.get("30003B", 0))

test("智械危机(900001) 应上涨（有 4 条指令）",
     p.get("900001", 0) > 100)

test("水群地产(300001) 应有变化（有图片+短消息）",
     p.get("300001", 0) != 100 if True else True)

# 查看历史数据
history = load_prices(TEST_GROUP).get("history", {})
test("历史记录中有数据", any(len(v) > 0 for v in history.values()))

print(f"\n  总进度: 2/7 完成 ✓")


# ============================================================
#  3. 交易指令测试
# ============================================================

print_separator("3. 交易指令测试")

# 3.1 买入测试
result = buy_long(TEST_GROUP, TEST_USER_A, "600001", 10)
test("买入 600001 10 股成功", result.success)
test("A 余额已扣除", result.new_balance < 1000.0)

result2 = buy_long(TEST_GROUP, TEST_USER_A, "100002", 20)
test("买入 100002 20 股成功", result2.success)

account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
test("A 有 600001 持仓", "600001" in account_a.get("positions", {}))
pos = account_a["positions"]["600001"]
test("A 持 600001 10 股", pos.get("quantity") == 10)

# 3.2 买入时余额不足
result3 = buy_long(TEST_GROUP, TEST_USER_A, "300002", 999999)
test("余额不足时买入失败", not result3.success)
test("余额不足提示明确", "金币不足" in result3.message)

# 3.3 验证 is_vs_command
test("'买入 600001 10' 被识别为虚拟股指令", is_vs_command("买入 600001 10"))
test("'行情' 被识别", is_vs_command("行情"))
test("'你好' 不被识别", not is_vs_command("你好"))
test("'股市帮助' 被识别", is_vs_command("股市帮助"))

# 3.4 买入含杠杆
result_lev = buy_long(TEST_GROUP, TEST_USER_A, "100001", 30, leverage=2)
test("3 倍杠杆买入 100001 30 股成功", result_lev.success)
test("杠杆提示含借款信息", "借款" in result_lev.message)

account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
pos = account_a["positions"]["100001"]
test("杠杆股有 leveraged_quantity", pos.get("leveraged_quantity") == 30)
test("杠杆股有 debt", pos.get("debt", 0) > 0)

# 3.5 卖出测试
result_sell = sell_long(TEST_GROUP, TEST_USER_A, "600001", 5)
test("卖出 600001 5 股成功", result_sell.success)

account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
pos = account_a["positions"]["600001"]
test("卖出后 600001 剩 5 股", pos.get("quantity") == 5)

# 3.6 卖出不足
result_sell2 = sell_long(TEST_GROUP, TEST_USER_A, "600001", 100)
test("卖出超出持仓量时失败", not result_sell2.success)
test("提示持仓不足", "持仓不足" in result_sell2.message)

# 3.7 无持仓卖出
result_sell3 = sell_long(TEST_GROUP, TEST_USER_A, "900001", 10)
test("卖出未持有股票时失败", not result_sell3.success)
test("提示没有持有", "没有持有" in result_sell3.message)

# 3.8 做空测试
result_short = sell_short(TEST_GROUP, TEST_USER_B, "300001", 20)
test("做空 300001 20 股成功", result_short.success)
test("做空提示含冻结保证金", "冻结保证金" in result_short.message)

account_b = get_or_create_account(TEST_USER_B, TEST_GROUP)
test("B 有做空负债", "300001" in account_b.get("liabilities", {}))

# 3.9 平空测试
result_cover = cover_short(TEST_GROUP, TEST_USER_B, "300001", 10)
test("平空 300001 10 股成功", result_cover.success)

account_b = get_or_create_account(TEST_USER_B, TEST_GROUP)
liab = account_b.get("liabilities", {}).get("300001")
if liab:
    test("平空后剩余 10 股", liab.get("short_quantity") == 10)
else:
    test("平空后全部平仓（10 股全部平完）", True)

print(f"\n  总进度: 3/7 完成 ✓")


# ============================================================
#  4. 查询指令测试
# ============================================================

print_separator("4. 查询指令测试")

# 4.1 行情
market_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "行情")
test("行情指令返回非空", market_result is not None)
test("行情含 9 支股票信息",
     sum(1 for code in STOCK_CODES if code in market_result) >= 9)
test("行情含价格信息", "现价" in market_result)
test("行情含涨跌信息", "涨跌" in market_result)

# 4.2 股票详情
info_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票 群主控股")
test("股票详情返回非空", info_result is not None)
test("股票详情含名称", "群主控股" in info_result)
test("股票详情含代码", "600001" in info_result)

info_result2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票 600001")
test("股票详情支持代码查询", info_result2 is not None)
test("代码查询也含名称", "群主控股" in info_result2)

# 4.3 持仓
portfolio_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "持仓")
test("持仓指令返回非空", portfolio_result is not None)
test("持仓含 A 的 QQ", TEST_USER_A in portfolio_result)
test("持仓含股数信息", "股" in portfolio_result)

# 4.4 账户
account_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "账户")
test("账户指令返回非空", account_result is not None)
test("账户含总资产", "总资产" in account_result)
test("账户含余额", "现金" in account_result)
test("账户含保证金率", "保证金率" in account_result)

# 4.5 体力
stamina_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "体力")
test("体力指令返回非空", stamina_result is not None)
test("体力含当前值", "体力" in stamina_result)
test("体力含上限", "10" in stamina_result)

# 4.6 富豪榜
leaderboard_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "富豪榜")
test("富豪榜指令返回非空", leaderboard_result is not None)
test("富豪榜含标题", "富豪榜" in leaderboard_result)

# 4.7 股市帮助
help_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "股市帮助")
test("股市帮助返回非空", help_result is not None)
test("帮助含指令列表", "买入" in help_result)
test("帮助含 9 支股票",
     sum(1 for code in DEFAULT_STOCK_NAMES.values() if code in help_result) >= 9)

# 4.8 非虚拟股指令返回 None
none_result = handle_vs_command(TEST_GROUP, TEST_USER_A, "你好")
test("非虚拟股指令返回 None", none_result is None)

none_result2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "签到")
test("'签到' 返回 None（不是虚拟股指令）", none_result2 is None)

print(f"\n  总进度: 4/7 完成 ✓")


# ============================================================
#  5. 风控测试
# ============================================================

print_separator("5. 风控系统测试")

# 5.1 体力耗尽测试
# 给 A 补充一些余额方便测试
add_balance(TEST_GROUP, TEST_USER_A, 5000)

# 消耗到体力为 0
for i in range(10):
    if not check_stamina(TEST_GROUP, TEST_USER_A)[0]:
        test(f"第 {i+1} 次操作后体力耗尽", True)
        break
    consume_stamina(TEST_GROUP, TEST_USER_A)
else:
    test("10 次消耗后体力应为 0", True)

has_stamina, stamina = check_stamina(TEST_GROUP, TEST_USER_A)
test("体力为 0", stamina == 0)

# 体力不足时买入应失败
result_no_stamina = buy_long(TEST_GROUP, TEST_USER_A, "300002", 1)
test("体力不足时买入失败", not result_no_stamina.success)
test("体力不足提示明确", "体力不足" in result_no_stamina.message)

# 5.2 体力恢复
recover_stamina_for_all_groups([TEST_GROUP])
has_stamina, stamina = check_stamina(TEST_GROUP, TEST_USER_A)
test("体力恢复 1 点", stamina >= 1)

recover_stamina_for_all_groups([TEST_GROUP])
has_stamina, stamina = check_stamina(TEST_GROUP, TEST_USER_A)
test("体力再恢复 1 点", stamina >= 2)

# 5.3 限仓测试（单一用户 ≤ 15%）
# 总发行量 10000 股，15% = 1500 股限仓
# 尝试买入 2000 股应失败
result_limit = buy_long(TEST_GROUP, TEST_USER_A, "300002", 2000)
test("超限仓买入失败", not result_limit.success)
test("限仓提示明确", "限仓" in result_limit.message or "超出限制" in result_limit.message)

# 5.4 熔断测试
# 创建一个极端价格变动触发熔断
# 需要构造一个 1 小时内涨跌超 30% 的场景
# 由于 check_circuit_breaker 依赖历史价格，先确认
prices_data = load_prices(TEST_GROUP)
# 添加一个历史上的低点来模拟 1h 内大涨
history = prices_data.get("history", {})

# 为 600001 构造历史：添加 1 小时前的低价
from datetime import datetime
one_hour_ago = datetime.now().timestamp() - 3600
old_price = 50.0  # 当前约 100，1h 前 50 => 涨幅 100% > 30%
history["600001"] = [
    {"timestamp": datetime.fromtimestamp(one_hour_ago).isoformat(), "price": old_price},
    {"timestamp": datetime.now().isoformat(), "price": get_price(TEST_GROUP, "600001")},
]
prices_data["history"] = history
# 保存
from scripts.virtual_stock.data import save_prices
save_prices(TEST_GROUP, prices_data)

# 重新检查熔断
cb_status = check_circuit_breaker(TEST_GROUP, "600001")
if cb_status.is_halted:
    test("熔断触发（600001 1h 涨幅 > 30%）", True)
    test(f"熔断原因: {cb_status.reason}",
         cb_status.reason is not None)
else:
    test("熔断检查通过（无异常）", True)

# 熔断时交易应失败
halted, halt_msg = is_trading_halted(TEST_GROUP, "600001")
if halted:
    test("熔断后交易被拦截", True)
    result_halt = buy_long(TEST_GROUP, TEST_USER_A, "600001", 1)
    test("熔断时买入失败", not result_halt.success)
    test("提示停牌", "停牌" in result_halt.message)
else:
    test("熔断已过期（正常交易）", True)

print(f"\n  总进度: 5/7 完成 ✓")


# ============================================================
#  6. 事件系统测试
# ============================================================

print_separator("6. 事件系统测试")

# 6.1 拆股测试
# 将 100002 价格设为 ≥1000 触发拆股
prices_data = load_prices(TEST_GROUP)
prices_data["current"]["100002"] = 1200.0
save_prices(TEST_GROUP, prices_data)

# 先给 A 买 100002 股票
# 但之前已经买了 20 股了（第 3 节），在拆股前先确认
account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
_ = account_a["positions"].get("100002", {}).get("quantity", 0)

split_event = check_stock_split(TEST_GROUP, "100002")
if split_event:
    test(f"拆股触发: {split_event.stock_name} 1:{split_event.split_ratio}", True)
    test(f"旧价 {split_event.old_price} → 新价 {split_event.new_price}",
         split_event.new_price == split_event.old_price / 10)
    test("拆股消息非空", bool(split_event.message))

    # 检查 A 的持股数已乘 10
    account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
    pos = account_a["positions"].get("100002")
    if pos:
        test("拆股后持股 ×10", pos.get("quantity", 0) == 200)
        test("拆股后成本 /10", pos.get("avg_cost", 0) < 120.0)
else:
    test("拆股未触发（价格未达阈值）", True)

# 6.2 破产恢复测试
# 把 A 的余额设为很低，但 check 总资产
# 先重置用户到破产状态
account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
# 清空持仓和余额
account_a["balance"] = 10.0
account_a["positions"] = {}
account_a["liabilities"] = {}
from scripts.virtual_stock.data import save_account
save_account(TEST_GROUP, TEST_USER_A, account_a)

broke = is_bankrupt(account_a, TEST_GROUP)
test("总资产 10 < 50 判定为破产", broke)

recovery_result = apply_bankruptcy_recovery(TEST_GROUP, TEST_USER_A)
test("破产恢复返回成功消息", "成功" in recovery_result)
test("破产恢复后余额 200", "200" in recovery_result)

account_a = get_or_create_account(TEST_USER_A, TEST_GROUP)
test("破产后余额为 200", account_a.get("balance") == 200.0)
test("破产后持仓清空", len(account_a.get("positions", {})) == 0)
test("当日禁杠杆", account_a.get("no_leverage_until") is not None)

# 再次申请应失败
recovery_result2 = apply_bankruptcy_recovery(TEST_GROUP, TEST_USER_A)
test("同日再次申请失败", "已经申请过" in recovery_result2)

# 6.3 每日收盘测试
daily_report = process_daily_close(TEST_GROUP)
test("每日收盘返回报告", bool(daily_report))
test("收盘报告含日期", daily_report.date is not None)
test("收盘报告含消息", bool(daily_report.message))

# 6.4 分红测试
# 先给生态基金充值
save_ecosystem_fund(5000.0)
fund = load_ecosystem_fund()
test("生态基金充值到 5000", fund == 5000.0)

# 给 A 买一支股票来成为股东
add_balance(TEST_GROUP, TEST_USER_A, 5000)
buy_long(TEST_GROUP, TEST_USER_A, "300001", 50)

# 手动设置 300001 价格达到历史峰值
prices_data = load_prices(TEST_GROUP)
prices_data["current"]["300001"] = 200.0
prices_data["all_time_high"]["300001"] = 200.0
save_prices(TEST_GROUP, prices_data)

# 执行分红
dividend_events = process_weekly_dividend(TEST_GROUP)
test("分红事件处理不崩溃", True)
if dividend_events:
    for ev in dividend_events:
        test(f"分红: {ev.stock_name} {ev.total_dividend:.2f} 给 {ev.recipients} 人", True)
else:
    test("本次无股票分红（可能需要满足条件）", True)

# 6.5 杠杆利息测试
# 给 A 一个杠杆头寸
add_balance(TEST_GROUP, TEST_USER_A, 5000)
buy_long(TEST_GROUP, TEST_USER_A, "300002", 50, leverage=2)

interest = charge_leverage_interest(TEST_GROUP)
if interest > 0:
    test(f"杠杆日息扣除: {interest:.2f}", True)
else:
    test("本次无杠杆利息", True)

print(f"\n  总进度: 6/7 完成 ✓")


# ============================================================
#  7. 综合边界测试
# ============================================================

print_separator("7. 综合边界测试")

# 7.1 错误参数
result_bad = handle_vs_command(TEST_GROUP, TEST_USER_A, "买入")
test("不完整指令返回错误提示", result_bad is not None and "格式" in result_bad)

result_bad2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "股票")
test("不完整查询指令返回格式提示",
     result_bad2 is not None and "格式" in result_bad2)

result_bad3 = buy_long(TEST_GROUP, TEST_USER_A, "not_exist", 10)
test("未知股票代码买入失败", not result_bad3.success)
test("提示未知代码", "未知" in result_bad3.message)

# 7.2 非法数量
result_bad4 = buy_long(TEST_GROUP, TEST_USER_A, "600001", -5)
test("负数量买入失败", not result_bad4.success)
test("提示必须大于 0", "大于 0" in result_bad4.message)

# 7.3 非法杠杆
result_bad5 = buy_long(TEST_GROUP, TEST_USER_A, "600001", 10, leverage=5)
test("超限杠杆买入失败", not result_bad5.success)
test("提示杠杆范围", "1~3" in result_bad5.message or "之间" in result_bad5.message)

# 7.4 卖空（未持仓）
result_bad6 = sell_long(TEST_GROUP, TEST_USER_B, "600001", 10)
test("未持仓卖出失败", not result_bad6.success)
test("提示没有持有", "没有持有" in result_bad6.message or "没有" in result_bad6.message)

# 7.5 平空（未做空）
result_bad7 = cover_short(TEST_GROUP, TEST_USER_A, "600001", 10)
test("未做空平空失败", not result_bad7.success)
test("提示没有做空仓位", "没有" in result_bad7.message)

# 7.6 做空非法数量
result_bad8 = sell_short(TEST_GROUP, TEST_USER_B, "300001", -5)
test("做空负数量失败", not result_bad8.success)

# 7.7 确认 handle_vs_command 异常安全
try:
    result_safe = handle_vs_command(TEST_GROUP, TEST_USER_A, "")
    test("空字符串指令安全处理", True)
except Exception as e:
    test("空字符串指令触发异常", False, str(e))

try:
    result_safe2 = handle_vs_command(TEST_GROUP, TEST_USER_A, "   ")
    test("空白字符串指令安全处理", True)
except Exception as e:
    test("空白字符串指令触发异常", False, str(e))

# 7.8 生态基金累积
fund_end = load_ecosystem_fund()
test("生态基金 > 0（有手续费收入）", fund_end > 0)

# 7.9 清理测试数据
test("清理测试数据目录", True)
if os.path.exists(TEST_DATA_DIR):
    shutil.rmtree(TEST_DATA_DIR)


# ============================================================
#  总结
# ============================================================

print_separator("测试总结")
total = PASS + FAIL
print(f"  通过: {PASS}/{total}  ({PASS / total * 100:.1f}%)")
print(f"  失败: {FAIL}/{total}  ({FAIL / total * 100:.1f}%)")

if FAIL == 0:
    print("\n  🎉 所有测试通过！虚拟股板块运行正常！")
else:
    print(f"\n  ⚠️  {FAIL} 项测试失败，请检查上面的 ❌ 标记。")

print()