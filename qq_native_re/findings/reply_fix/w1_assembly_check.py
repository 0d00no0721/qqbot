#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""W1 装配链测试：把 _call_model_script 换成捕获桩，验证 stdin JSON 真的带上了 target。

不走网络、不调模型——只验证 decision_engine 内部「组装 → 下发」这一段。
用法：cd E:\QQbot; $env:PYTHONIOENCODING="utf-8"; & ".\venv\Scripts\python" qq_native_re\findings\reply_fix\w1_assembly_check.py
"""
import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from scripts.decision_engine import DecisionEngine  # noqa: E402

GID = "284517232"
CAPTURED = []
all_ok = True


def check(name, cond, extra=""):
    global all_ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))
    all_ok = all_ok and bool(cond)


async def main():
    engine = DecisionEngine()

    # ---------- 桩：捕获每次下发的 stdin JSON，返回固定回复 ----------
    async def fake_call(script_path, gid, max_tokens=500, api_key="", target=None):
        avoid = [r for r in engine._recent_bot_replies.get(gid, ()) if r]
        CAPTURED.append({
            "script": os.path.basename(script_path),
            "gid": gid,
            "target": target,
            "avoid_recent": avoid,
        })
        return "深圳今天多云，出门带伞。"

    engine._call_model_script = fake_call

    # ============ 场景 1：@bot 路径 ============
    print("===== 场景1  @bot 强制回复路径 =====")
    engine.add_message(GID, "1305489180", "UC", "软的翻出去")
    engine.add_message(GID, "202890590", "多喝热水", "后台可以关允许摇晃感应")
    should, reply = await engine.should_reply(
        message="今日深圳天气", user_id="1305489180", group_id=GID,
        is_at_bot=True, sender_nickname="UC",
    )
    check("@bot 路径回复成功", should and reply == "深圳今天多云，出门带伞。",
          f"should={should} reply={reply!r}")
    check("生成被调用（主+备均捕获）", len(CAPTURED) >= 1, f"n={len(CAPTURED)}")
    c = CAPTURED[0]
    check("target 非空", c["target"] is not None, str(c["target"]))
    check("target.nickname == 'UC'", (c["target"] or {}).get("nickname") == "UC")
    check("target.uid == '1305489180'", (c["target"] or {}).get("uid") == "1305489180")
    check("target.content == '今日深圳天气'",
          (c["target"] or {}).get("content") == "今日深圳天气")
    check("首轮 avoid_recent 为空（尚无历史回复）", c["avoid_recent"] == [])
    check("debug 暴露回应目标", "UC" in engine.get_debug_info().get("回应目标", ""),
          engine.get_debug_info().get("回应目标", ""))

    # ============ 场景 2：防复读队列滚动 ============
    print("===== 场景2  防复读队列（应在生成后 append、不回灌本轮） =====")
    CAPTURED.clear()
    should2, reply2 = await engine.should_reply(
        message="今天深圳热不热", user_id="1305489180", group_id=GID,
        is_at_bot=True, sender_nickname="UC",
    )
    # 桩每次都返回同一句话 → 会命中**既有的**精确去重（should_reply=False）。
    # 这恰好测出指标口径：相似度在"生成成功"时就记录，早于去重判定。
    seen = CAPTURED[0]["avoid_recent"] if CAPTURED else []
    check("第二轮下发 avoid_recent 含第一轮回复（本轮回复未回灌）",
          seen == ["深圳今天多云，出门带伞。"], f"seen={seen}")
    check("重复回复被既有精确去重拦截（未改动该行为）", should2 is False,
          f"should2={should2} reply2={reply2!r}")
    check("_recent_bot_replies 记录的是'生成过'的回复数（=2，含被去重的那条）",
          len(engine._recent_bot_replies[GID]) == 2,
          f"len={len(engine._recent_bot_replies[GID])}")
    check("队列 maxlen == 3", engine._recent_bot_replies[GID].maxlen == 3)

    # ============ 场景 3：非 @ 路径无 target 泄漏 ============
    print("===== 场景3  _build_target 边界（空内容 → None，回落旧 prompt） =====")
    check("空 message → None", DecisionEngine._build_target("", "1", "A") is None)
    check("纯空白 message → None", DecisionEngine._build_target("   ", "1", "A") is None)
    check("None nickname → 空串（不炸）",
          DecisionEngine._build_target("hi", "1", None)["nickname"] == "")
    check("None user_id → 空串（不炸）",
          DecisionEngine._build_target("hi", None, "A")["uid"] == "")
    t = DecisionEngine._build_target("  今日深圳天气  ", "1305489180", " UC ")
    check("content/nickname 去空白", t["content"] == "今日深圳天气" and t["nickname"] == "UC",
          str(t))

    # ============ 场景 4：相似度指标真的算出来了 ============
    print("===== 场景4  _record_bot_reply_metric（只记指标、不拦截） =====")
    import difflib
    eng2 = DecisionEngine()
    eng2._record_bot_reply_metric("g", "摇一摇广告真缺德")
    eng2._record_bot_reply_metric("g", "摇一摇广告真缺德啊")   # 高相似
    eng2._record_bot_reply_metric("g", "深圳今天多云")         # 低相似
    q = list(eng2._recent_bot_replies["g"])
    r = difflib.SequenceMatcher(None, "摇一摇广告真缺德啊", "摇一摇广告真缺德").ratio()
    check("高相似对能算出 >0.75", r > 0.75, f"ratio={r:.2f}")
    check("队列保留最近 3 条", len(q) == 3, str(q))
    check("指标方法返回 None（无拦截语义）",
          eng2._record_bot_reply_metric("g", "x") is None)

    # ============ 场景 5：maxlen 溢出只留 3 条 ============
    print("===== 场景5  队列溢出 =====")
    for i in range(6):
        eng2._record_bot_reply_metric("g", f"回复{i}")
    q2 = list(eng2._recent_bot_replies["g"])
    check("滚动后仍为 3 条且为最新 3 条",
          len(q2) == 3 and q2[-1] == "回复5", str(q2))


    # ============ 场景 6：CQ 码消息走完整 should_reply（D2 端到端） ============
    print("===== 场景6  CQ 码消息端到端：@bot+纯空白 应回落旧 prompt =====")
    eng3 = DecisionEngine()
    caps6 = []

    async def fake3(script_path, gid, max_tokens=500, api_key="", target=None):
        caps6.append({"script": os.path.basename(script_path), "target": target})
        return "在的～"

    eng3._call_model_script = fake3
    eng3.add_message(GID, "2668851638", "bot", "上一句")

    # 6a: @bot + 纯空白（reverse_bot 的空消息守卫用 strip 前字符串，会原样送进来）
    ok6a, _ = await eng3.should_reply(
        message="[CQ:at,qq=2668851638] ", user_id="1", group_id=GID,
        is_at_bot=True, sender_nickname="小明",
    )
    c6a = caps6[-1]["target"] if caps6 else "NO_CALL"
    check("6a @bot+纯空白 → target 为 None（回落旧 prompt，不编造被回应的话）",
          c6a is None, f"target={c6a!r} should={ok6a}")

    # 6b: 纯图片消息
    caps6.clear()
    await eng3.should_reply(
        message="[CQ:image,file=a.png,url=https://x/y]", user_id="2", group_id=GID,
        is_at_bot=True, sender_nickname="小红",
    )
    c6b = caps6[-1]["target"] if caps6 else "NO_CALL"
    check("6b 纯图片 → target 为 None", c6b is None, f"target={c6b!r}")

    # 6c: @bot + 正文 → 剥掉 CQ 码，只留正文
    caps6.clear()
    await eng3.should_reply(
        message="[CQ:at,qq=2668851638] \n今日深圳天气", user_id="1305489180", group_id=GID,
        is_at_bot=True, sender_nickname="UC",
    )
    c6c = caps6[-1]["target"] if caps6 else "NO_CALL"
    check("6c @bot+正文 → content 已剥 CQ 码",
          isinstance(c6c, dict) and c6c.get("content") == "今日深圳天气", f"target={c6c!r}")
    check("6c 其余字段仍正确",
          isinstance(c6c, dict) and c6c.get("nickname") == "UC"
          and c6c.get("uid") == "1305489180", f"target={c6c!r}")

    # 6d: 图片 + 文字
    caps6.clear()
    await eng3.should_reply(
        message="[CQ:image,file=a.png] 看这个", user_id="3", group_id=GID,
        is_at_bot=True, sender_nickname="小刚",
    )
    c6d = caps6[-1]["target"] if caps6 else "NO_CALL"
    check("6d 图片+文字 → 只留文字",
          isinstance(c6d, dict) and c6d.get("content") == "看这个", f"target={c6d!r}")


asyncio.run(main())
print()
print("总体:", "ALL PASS" if all_ok else "存在 FAIL")
sys.exit(0 if all_ok else 1)
