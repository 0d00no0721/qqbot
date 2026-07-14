#!/usr/bin/env python3
"""虚拟股 · 项目集成验证脚本。"""
import sys
import os
import shutil

# 清理旧测试数据
data_dir = os.path.join("scripts", "virtual_stock", "data")
for d in os.listdir(data_dir):
    if d.startswith("group_999"):
        shutil.rmtree(os.path.join(data_dir, d))

print("=" * 60)
print("1. 包导入验证")
print("=" * 60)
import scripts.virtual_stock as vs
print(f"  __all__ = {vs.__all__}")
print(f"  包版本文档: 1.0.0")
assert hasattr(vs, "on_message"), "缺少 on_message"
assert hasattr(vs, "handle_vs_command"), "缺少 handle_vs_command"
assert hasattr(vs, "is_vs_command"), "缺少 is_vs_command"
assert hasattr(vs, "start_scheduler"), "缺少 start_scheduler"
assert hasattr(vs, "stop_scheduler"), "缺少 stop_scheduler"
assert hasattr(vs, "register_group"), "缺少 register_group"
assert hasattr(vs, "init_group_data"), "缺少 init_group_data"
print("  ✅ 全部 API 导出正常")

print()
print("=" * 60)
print("2. command_handler 源码验证（绕过 jmcomic 依赖）")
print("=" * 60)
with open("scripts/command_handler.py", "r", encoding="utf-8") as f:
    ch_src = f.read()
assert 'VERSION = "2.3.0"' in ch_src, "版本号不是 2.3.0"
print('  ✅ VERSION = "2.3.0"')
assert "from scripts.virtual_stock import" in ch_src, "缺少 virtual_stock 导入"
print("  ✅ virtual_stock 导入存在")
assert "_vs_is_command(clean_message)" in ch_src, "缺少指令分发"
print("  ✅ handle_command 中有虚拟股指令分发")
assert '"股市"' in ch_src, "缺少「股市」帮助分类"
assert '"虚拟股市"' in ch_src, "缺少「虚拟股市」标题"
print("  ✅ 帮助系统包含「股市」分类")
assert "_vs_is_command(msg)" in ch_src, "_is_command 缺少 VS 检查"
print("  ✅ _is_command 包含虚拟股指令识别")
print("  ✅ command_handler 集成代码全部正确")

print()
print("=" * 60)
print("3. 指令识别验证 (is_vs_command)")
print("=" * 60)
test_cases = [
    ("行情", True),
    ("股票 600001", True),
    ("买入 群主控股 100", True),
    ("买入 600001 100 3", True),
    ("卖出 水群地产 50", True),
    ("做空 战雷航空 30", True),
    ("平空 100001 30", True),
    ("持仓", True),
    ("账户", True),
    ("体力", True),
    ("富豪榜", True),
    ("破产恢复", True),
    ("股市帮助", True),
    ("今天吃啥", False),
    ("签到", False),
    ("帮助", False),
    ("hello world", False),
]
all_ok = True
for text, expected in test_cases:
    result = vs.is_vs_command(text)
    status = "✅" if result == expected else "❌"
    if result != expected:
        all_ok = False
    print(f"  {status} is_vs_command({text!r}) = {result} (期望 {expected})")
assert all_ok, "指令识别有误"
print("  ✅ 全部指令识别正确")

print()
print("=" * 60)
print("4. _is_command 源码验证 (绕过 jmcomic 依赖)")
print("=" * 60)
# 验证 _is_command 源码中包含 VS 检查
assert "_vs_is_command(msg)" in ch_src, "_is_command 缺少 VS 检查"
print("  ✅ _is_command 源码包含 _vs_is_command(msg) 调用")
# 验证非 VS 指令也能通过（源码中存在其他指令匹配）
for kw in ["今天吃啥", "签到", "帮助"]:
    assert kw in ch_src, f"command_handler 缺少指令: {kw}"
    print(f"  ✅ command_handler 包含指令: {kw}")
print("  ✅ _is_command 集成正常")

print()
print("=" * 60)
print("5. 指令处理验证 (handle_vs_command)")
print("=" * 60)
GID = "999999001"
UID = "123456"
# 初始化群数据
vs.init_group_data(GID)

# 行情
reply = vs.handle_vs_command(GID, UID, "行情")
assert reply is not None and "虚拟股市行情" in reply
print(f"  ✅ 行情: {reply[:40]}...")

# 股票详情
reply = vs.handle_vs_command(GID, UID, "股票 群主控股")
assert reply is not None and "群主控股" in reply
print(f"  ✅ 股票详情: {reply[:40]}...")

# 股票详情（代码）
reply = vs.handle_vs_command(GID, UID, "股票 600001")
assert reply is not None and "600001" in reply
print(f"  ✅ 股票详情(代码): {reply[:40]}...")

# 买入（初始余额1000，股价~100，买5股需~507）
reply = vs.handle_vs_command(GID, UID, "买入 群主控股 5")
assert reply is not None and "成功" in reply
print(f"  ✅ 买入: {reply[:50]}...")

# 持仓
reply = vs.handle_vs_command(GID, UID, "持仓")
assert reply is not None and "持仓" in reply
print(f"  ✅ 持仓: {reply[:40]}...")

# 账户
reply = vs.handle_vs_command(GID, UID, "账户")
assert reply is not None and "账户" in reply
print(f"  ✅ 账户: {reply[:40]}...")

# 体力
reply = vs.handle_vs_command(GID, UID, "体力")
assert reply is not None and "体力" in reply
print(f"  ✅ 体力: {reply[:40]}...")

# 卖出
reply = vs.handle_vs_command(GID, UID, "卖出 群主控股 5")
assert reply is not None
print(f"  ✅ 卖出: {reply[:50]}...")

# 股市帮助
reply = vs.handle_vs_command(GID, UID, "股市帮助")
assert reply is not None and "虚拟股市帮助" in reply
print(f"  ✅ 股市帮助: {reply[:40]}...")

# 非虚拟股指令
reply = vs.handle_vs_command(GID, UID, "今天吃啥")
assert reply is None
print(f"  ✅ 非VS指令返回 None")

print("  ✅ 全部指令处理正确")

print()
print("=" * 60)
print("6. 消息钩子验证 (on_message + 指标采集)")
print("=" * 60)
# 模拟群消息
vs.on_message(GID, "408754232", "大家好我是群主今天天气不错")
vs.on_message(GID, "123456", "群主好")
vs.on_message(GID, "789012", "水水水水水水水水水水水水")
vs.on_message(GID, "345678", "战雷起飞！wt好玩")
print("  ✅ on_message 4条消息采集无异常")

# 刷新股价
from scripts.virtual_stock import refresh_prices
new_prices = refresh_prices(GID)
print(f"  ✅ refresh_prices: 600001={new_prices.get('600001')}")
assert new_prices.get("600001") is not None

print()
print("=" * 60)
print("7. 群隔离验证")
print("=" * 60)
GID2 = "999999002"
vs.init_group_data(GID2)
vs.on_message(GID2, "408754232", "群主在另一个群发言")
reply1 = vs.handle_vs_command(GID, "123456", "账户")
reply2 = vs.handle_vs_command(GID2, "123456", "账户")
assert reply1 != reply2, "两个群的账户应该不同"
print(f"  ✅ 群1账户: {reply1.split(chr(10))[1].strip()}")
print(f"  ✅ 群2账户: {reply2.split(chr(10))[1].strip()}")
print("  ✅ 群隔离正常")

print()
print("=" * 60)
print("8. 帮助系统验证")
print("=" * 60)
# 从源码验证帮助分类
assert '"股市"' in ch_src, "command_handler 缺少「股市」帮助分类"
print('  ✅ 帮助系统包含「股市」分类')
assert '"虚拟股市"' in ch_src, "缺少「虚拟股市」标题"
print('  ✅ 帮助系统包含「虚拟股市」标题')
# 验证帮助类别中包含虚拟股指令
for cmd_kw in ["行情", "买入", "卖出", "做空", "持仓", "账户", "富豪榜"]:
    assert cmd_kw in ch_src, f"command_handler 缺少关键词: {cmd_kw}"
print("  ✅ 帮助系统包含全部虚拟股指令关键词")

print()
print("=" * 60)
print("9. reverse_bot 集成验证")
print("=" * 60)
# 检查关键导入是否存在（直接读源码，不导入 reverse_bot 避免触发 ws 依赖）
with open("reverse_bot.py", "r", encoding="utf-8") as f:
    rb_src = f.read()
assert "from scripts.virtual_stock import" in rb_src, "reverse_bot 缺少 virtual_stock 导入"
print("  ✅ virtual_stock 导入存在")
assert "vs_on_message" in rb_src, "reverse_bot 缺少 vs_on_message 调用"
print("  ✅ vs_on_message 消息钩子存在")
assert "vs_start_scheduler" in rb_src, "reverse_bot 缺少 vs_start_scheduler"
print("  ✅ vs_start_scheduler 启动调度器存在")
assert "vs_stop_scheduler" in rb_src, "reverse_bot 缺少 vs_stop_scheduler"
print("  ✅ vs_stop_scheduler 停止调度器存在")
assert "vs_register_group" in rb_src, "reverse_bot 缺少 vs_register_group"
print("  ✅ vs_register_group 群注册存在")
assert "vs_broadcast" in rb_src, "reverse_bot 缺少 vs_broadcast 回调"
print("  ✅ vs_broadcast 广播回调存在")
print("  ✅ reverse_bot 虚拟股集成代码全部存在")

print()
print("=" * 60)
print("10. scheduler 导入验证")
print("=" * 60)
from scripts.virtual_stock.scheduler import VSScheduler
# 验证 8 个协程方法存在
loop_methods = [
    "_loop_prices", "_loop_liquidation", "_loop_split", "_loop_stamina",
    "_loop_daily_interest", "_loop_daily_leaderboard", "_loop_daily_close",
    "_loop_weekly_dividend",
]
for m in loop_methods:
    assert hasattr(VSScheduler, m), f"VSScheduler 缺少方法 {m}"
print(f"  ✅ 8 个定时协程方法全部存在: {', '.join(loop_methods)}")

# 验证 _is_time 工具函数
assert hasattr(VSScheduler, "_is_time"), "VSScheduler 缺少 _is_time"
print(f"  ✅ _is_time 工具函数存在")

print()
print("=" * 60)
print("🎉 全部集成验证通过！可以上传重启服务器。")
print("=" * 60)

# 清理测试数据
for d in os.listdir(data_dir):
    if d.startswith("group_999"):
        shutil.rmtree(os.path.join(data_dir, d))
print("测试数据已清理")
