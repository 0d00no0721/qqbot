# -*- coding: utf-8 -*-
"""算卦/占卜 — 梅花易数起卦 + AI 解读（走中央模型池）。"""

import asyncio
import json
import os
import re
import time

try:
    from scripts.llm_pool import chat, chat_sync, available_models, LLMPoolExhausted
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import chat, chat_sync, available_models, LLMPoolExhausted

from . import _state
from ._registry import register, CommandContext

try:
    from zhdate import ZhDate
    HAS_ZHDATE = True
except ImportError:
    HAS_ZHDATE = False


# ========== 算卦/占卜数据 ==========
YIJING_DATA_FILE = os.path.join(_state.SCRIPT_DIR, "yijing_structured_fixed.json")

TRIGRAM_NUM = {"乾": 1, "兌": 2, "離": 3, "震": 4, "巽": 5, "坎": 6, "艮": 7, "坤": 8}

GUA_TABLE_8X8 = [
    [None,  "乾",  "夬",  "大有","大壯","小畜","需",  "大畜","泰" ],
    [None,  "履",  "兌",  "睽",  "歸妹","中孚","節",  "損",  "臨" ],
    [None,  "同人","革",  "離",  "豐",  "家人","既濟","賁",  "明夷"],
    [None,  "无妄","隨",  "噬嗑","震",  "益",  "屯",  "頤",  "復" ],
    [None,  "姤",  "大過","鼎",  "恆",  "巽",  "井",  "蠱",  "升" ],
    [None,  "訟",  "困",  "未濟","解",  "渙",  "坎",  "蒙",  "師" ],
    [None,  "遯",  "咸",  "旅",  "小過","漸",  "蹇",  "艮",  "謙" ],
    [None,  "否",  "萃",  "晉",  "豫",  "觀",  "比",  "剝",  "坤" ],
]
NUM_TRIGRAM = {v: k for k, v in TRIGRAM_NUM.items()}

BITS_TO_TRIGRAM_NUM = {
    (1, 1, 1): 1, (1, 1, 0): 2, (1, 0, 1): 3, (1, 0, 0): 4,
    (0, 1, 1): 5, (0, 1, 0): 6, (0, 0, 1): 7, (0, 0, 0): 8,
}

WUXING_MAP = {"乾": "金", "兌": "金", "震": "木", "巽": "木", "坎": "水", "離": "火", "艮": "土", "坤": "土"}

EVENT_KEYWORDS = {
    1: ["感情", "恋爱", "婚姻", "爱情", "对象", "交往", "姻缘"],
    2: ["事业", "工作", "职场", "创业", "生意", "公司", "职业"],
    3: ["财运", "投资", "股票", "基金", "赚钱", "经济", "财富"],
    4: ["健康", "身体", "疾病", "生病", "养生", "医疗"],
    5: ["考试", "学业", "高考", "考研", "学习", "毕业", "论文"],
    6: ["出行", "旅行", "旅游", "搬家", "迁移", "远行"],
}

gua_table = {}
gua_data = {}
gua_yinyang = {}
bian_gua_map = {}


def load_yijing_data():
    """从 JSON 加载六十四卦数据"""
    try:
        with open(YIJING_DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data["hexagrams"]
    except Exception as e:
        if _state.bot_logger:
            _state.bot_logger.info(f"[算卦] 加载易经数据失败: {e}")
        return None


def init_divination_data():
    """初始化卦表、阴阳数组、变卦映射"""
    global gua_table, gua_data, gua_yinyang, bian_gua_map
    hexagrams = load_yijing_data()
    if not hexagrams:
        if _state.bot_logger:
            _state.bot_logger.info("[算卦] 未加载到易经数据，算卦功能不可用")
        return False

    for u_idx in range(1, 9):
        for l_idx in range(1, 9):
            name = GUA_TABLE_8X8[u_idx - 1][l_idx]
            if name:
                gua_table[(u_idx, l_idx)] = name

    for h in hexagrams:
        gua_data[h["name"]] = h
        yy = []
        for ln in h["lines"]:
            if "九" in ln["position"]:
                yy.append(1)
            elif "六" in ln["position"]:
                yy.append(0)
        gua_yinyang[h["name"]] = yy[:6]

    for name, yy in gua_yinyang.items():
        if len(yy) != 6:
            continue
        for dong_yao in range(1, 7):
            new_yy = yy.copy()
            new_yy[dong_yao - 1] = 1 - new_yy[dong_yao - 1]
            lower_bits = tuple(new_yy[:3])
            upper_bits = tuple(new_yy[3:])
            lower_num = BITS_TO_TRIGRAM_NUM.get(lower_bits)
            upper_num = BITS_TO_TRIGRAM_NUM.get(upper_bits)
            if lower_num and upper_num:
                bian_name = gua_table.get((upper_num, lower_num), "未知")
            else:
                bian_name = "未知"
            bian_gua_map[(name, dong_yao)] = bian_name

    return True


# 初始化算卦数据
_div_init_ok = init_divination_data()


def _get_lunar_info(dt):
    """获取农历信息（年地支序数、月、日、时辰序数），失败回退公历"""
    try:
        if HAS_ZHDATE:
            lunar = ZhDate.from_datetime(dt)
            year_dizhi = (lunar.lunar_year - 4) % 12 + 1
            month = lunar.lunar_month
            day = lunar.lunar_day
        else:
            raise ImportError("无农历库")
    except Exception:
        year_dizhi = (dt.year - 4) % 12 + 1
        month = dt.month
        day = dt.day

    hour = dt.hour
    hour_index = 1 if hour == 23 else (hour + 1) // 2 + 1
    return year_dizhi, month, day, hour_index


def _classify_event(text: str) -> int:
    """对事件描述进行关键词分类"""
    if not text:
        return 0
    for cat, keywords in EVENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return cat
    return 0


def perform_divination(nickname: str, qq_id: str, event_text: str, dt=None) -> dict:
    """执行确定性算卦，返回卦象 JSON"""
    if dt is None:
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        from datetime import datetime
        dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")

    nickname = nickname or ""
    event_text = event_text or ""
    nick_len = int(len(nickname))
    nick_codes = int(sum(ord(c) for c in nickname))
    qq_str = qq_id or ""
    qq_sum = int(sum(int(d) for d in qq_str if d.isdigit()))
    qq_last2 = int(qq_str[-2:]) if len(qq_str) >= 2 and qq_str[-2:].isdigit() else 0
    event_codes = int(sum(ord(c) for c in event_text))
    event_cat = int(_classify_event(event_text))
    if not (0 <= event_cat <= 6):
        event_cat = 0
    seconds = int(dt.second)

    year_dizhi, month, day, hour_index = _get_lunar_info(dt)

    shang_base = (year_dizhi * 3) + (month * 2) + nick_len
    xia_base = (day * 4) + (hour_index * 2) + qq_last2
    dong_base = nick_codes + qq_sum + event_codes + seconds

    shang_num = shang_base % 8
    if shang_num == 0:
        shang_num = 8
    xia_num = xia_base % 8
    if xia_num == 0:
        xia_num = 8
    dong_yao = dong_base % 6
    if dong_yao == 0:
        dong_yao = 6

    ben_name = gua_table.get((shang_num, xia_num), "未知")
    ben_info = gua_data.get(ben_name, {})
    bian_name = bian_gua_map.get((ben_name, dong_yao), "")

    if dong_yao >= 4:
        ti_name = NUM_TRIGRAM.get(xia_num, "")
        yong_name = NUM_TRIGRAM.get(shang_num, "")
    else:
        ti_name = NUM_TRIGRAM.get(shang_num, "")
        yong_name = NUM_TRIGRAM.get(xia_num, "")

    ti_wx = WUXING_MAP.get(ti_name, "")
    yong_wx = WUXING_MAP.get(yong_name, "")

    wx_sheng = {"金": "水", "水": "木", "木": "火", "火": "土", "土": "金"}
    wx_ke = {"金": "木", "木": "土", "土": "水", "水": "火", "火": "金"}

    if ti_wx == yong_wx:
        sheng_ke = "比和"
    elif wx_sheng.get(ti_wx) == yong_wx:
        sheng_ke = "体生用"
    elif wx_sheng.get(yong_wx) == ti_wx:
        sheng_ke = "用生体"
    elif wx_ke.get(ti_wx) == yong_wx:
        sheng_ke = "体克用"
    elif wx_ke.get(yong_wx) == ti_wx:
        sheng_ke = "用克体"
    else:
        sheng_ke = "未知"

    if sheng_ke in ("用生体", "体克用", "比和"):
        ji_xiong = "吉"
    elif sheng_ke == "体生用":
        ji_xiong = "平"
    else:
        ji_xiong = "凶"

    dong_yao_ci = ""
    dong_yao_xiang = ""
    if ben_info:
        lines_list = ben_info.get("lines", [])
        if 1 <= dong_yao <= len(lines_list):
            line_obj = lines_list[dong_yao - 1]
            dong_yao_ci = line_obj.get("text", "")
            dong_yao_xiang = line_obj.get("xiang", "")

    return {
        "ben_gua": ben_name, "bian_gua": bian_name, "dong_yao": dong_yao,
        "dong_yao_ci": dong_yao_ci, "dong_yao_xiang": dong_yao_xiang,
        "ti_gua": ti_name, "yong_gua": yong_name,
        "ti_wuxing": ti_wx, "yong_wuxing": yong_wx,
        "sheng_ke": sheng_ke, "ji_xiong": ji_xiong, "wen_lei": event_cat,
    }


async def call_divination_api(divination_json: dict, event_text: str) -> str:
    """调用中央模型池解读卦象（完全异步，池内自动故障转移）"""
    event_desc = event_text.strip() if event_text.strip() else "一般性询问"
    system_prompt = "你是一位精通易经的古代卦师，根据卦象为问卜者指点迷津。回答需简洁玄妙，60字左右，通俗易懂。"

    dong_yao_text = f"第{divination_json['dong_yao']}爻"
    user_message = (
        f"占卜结果如下：\n问卜内容：{event_desc}\n"
        f"本卦：{divination_json['ben_gua']}"
    )
    if divination_json.get("bian_gua"):
        user_message += f"，变卦：{divination_json['bian_gua']}"
    user_message += f"\n动爻：{dong_yao_text}"
    if divination_json.get("dong_yao_ci"):
        user_message += f"，爻辞：\"{divination_json['dong_yao_ci']}\""
    user_message += (
        f"\n体卦：{divination_json['ti_gua']}（{divination_json['ti_wuxing']}）"
        f"，用卦：{divination_json['yong_gua']}（{divination_json['yong_wuxing']}）"
        f"\n生克关系：{divination_json['sheng_ke']}"
        f"\n整体吉凶：{divination_json['ji_xiong']}"
    )

    content, _model = await chat(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        label="divination",
        max_tokens=_state.DIVINATION_MAX_TOKENS,
        temperature=0.7,
        timeout=15.0,
    )
    if not content:
        if _state.bot_logger:
            _state.bot_logger.info("[占卜] 警告: AI 返回了空内容")
        return "卦象已出，但解读为空，请换个描述重试。"
    return content


def _divination_match(msg: str):
    """算卦/占卜前缀匹配（要求空格分隔）"""
    for p in ("算卦", "占卜"):
        if msg == p or msg.startswith(p + " "):
            return p
    return False


@register("算卦", match_type="custom", pattern=_divination_match)
async def cmd_divination(ctx: CommandContext, prefix: str) -> bool:
    event_text = re.sub(r"^(算卦|占卜)\s*", "", ctx.clean_message).strip()
    raw_event = event_text
    event_text = re.sub(r'[​‌‍⁠﻿ ]', '', event_text).strip()
    if not event_text:
        event_text = "一般性询问"
    ctx.log(f"[占卜] 原始输入: {{{raw_event}}}, 清理后: {{{event_text}}}")

    if not _div_init_ok:
        await ctx.send("算卦数据未加载，请联系管理员。", at_user=ctx.uid_str)
        return True

    loop = asyncio.get_event_loop()
    try:
        div_result = await loop.run_in_executor(
            None, perform_divination, ctx.nickname, ctx.uid_str, event_text, None)
        if div_result["ben_gua"] == "未知":
            await ctx.send("算卦数据异常，请联系管理员。", at_user=ctx.uid_str)
            return True
        ctx.log(f"[占卜] 调用 AI，事件描述: {event_text[:50]}")
        interpretation = await call_divination_api(div_result, event_text)
        if not interpretation or not interpretation.strip():
            ctx.log("[占卜] 警告: interpretation 为空，使用回退消息")
            interpretation = (
                f"本卦：{div_result['ben_gua']}，动爻：{div_result['dong_yao']}\n"
                f"体用：{div_result['sheng_ke']}，{div_result['ji_xiong']}")
        await ctx.send(interpretation, at_user=ctx.uid_str)
    except Exception as e:
        ctx.log(f"[算卦] 失败: {e}")
        import traceback
        traceback.print_exc()
        try:
            fallback = await loop.run_in_executor(
                None, perform_divination, ctx.nickname, ctx.uid_str, event_text, None)
            fallback_msg = (
                f"卦象已出，但解读服务暂时不可用。\n"
                f"本卦：{fallback['ben_gua']}，动爻：{fallback['dong_yao']}\n"
                f"体用：{fallback['sheng_ke']}，{fallback['ji_xiong']}")
            await ctx.send(fallback_msg, at_user=ctx.uid_str)
        except Exception as e2:
            ctx.log(f"[算卦] 回退也失败: {e2}")
            import traceback
            traceback.print_exc()
            await ctx.send("占卜功能暂时不可用，请稍后再试。", at_user=ctx.uid_str)
    return True
