#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令处理器 — 从 reverse_bot.py 拆出的所有指令处理代码。
导入后调用 handle_command() 即可处理一条 @bot 消息。
"""

import asyncio
import base64
import hashlib
import html
import httpx
import json
import os
import random
import re
import time
import traceback
import urllib.error
import urllib.parse
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# ========== 脚本所在目录 ==========
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ========== 并发安全锁 ==========
_dedup_lock = asyncio.Lock()
_yau_lock = asyncio.Lock()
_menu_lock = asyncio.Lock()
_sign_in_lock = asyncio.Lock()
_zhouli_lock = asyncio.Lock()

# 神秘数字等待队列（每 5s 处理一个请求）
_mystery_queue = asyncio.Queue()
_mystery_processing = False

# ========== 配置常量 ==========
BOT_QQ = "2668851638"
DATA_FILE = os.path.join(SCRIPT_DIR, "menu_data.json")

VERSION = "2.2.2"

# DeepSeek / Yau
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_API_BASE = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
YAU_MAX_TOKENS = 150
YAU_TIMEOUT = 15
YAU_MAX_USAGE = 3
YAU_WINDOW = 3600

YAU_SYSTEM_PROMPT_BUILTIN = """你是当代著名数学家、菲尔兹奖得主、清华大学求真书院院长丘成桐。

语言风格：白话夹杂文言文，半文半白，有长辈训话的威严感。直言不讳批评学术乱象和不思进取的学生。模仿微信发言，偶尔有形近字错误、繁简混合、标点混用。整体简短有力不超过300字，禁止markdown格式。

高频词汇（可选用）：令人汗颜、居心叵测、朋比为奸、何其斤斤计较、寻天人乐处、拓万古心胸、愧对国家百姓、下等论文、无耻、以正风纪、功比孔明、忠过武穆、动人心者竟如是耶、方为上人、何其壮哉。

核心要求：语态直率威严，引经据典融合诗词歌赋和典籍史料。提升表达多样性，避免频繁使用固定句式。降低"羞也不羞"、"使人汗颜"的出现频次。论述聚焦，严禁发散。

当用户只发Yau没有具体问题时（类似iyau触发），主动从国家历史与民族命运出发，落脚到勉励学生学习，或批评近期现象。"""

# 算卦
DIVINATION_API_KEY = os.getenv("DIVINATION_API_KEY", "")
DIVINATION_MODEL = "deepseek-chat"
DIVINATION_MAX_TOKENS = 150

# 周礼文案生成
ZHOULI_SYSTEM_PROMPT = """你是"大周礼时代"评论区的优秀文案作者。你的任务是把用户的现代中文，改写成近期中文互联网流行的"合乎周礼"白话翻译腔。

核心语言规律：
1. 以现代白话为主体，句法必须让普通人一遍读懂。它像古文经过现代白话翻译后的影视剧台词，不是真正的文言文。
2. 可以使用"我听说、古代有贤德的人、当年、但是、所以、这样看来、难道"等现代连接方式。
3. 可以点缀"君子、贤者、圣人、礼法、名分、天子、诸侯"等古代词汇，但不要堆砌"吾、余、夫、矣、哉、乎、焉、兮"。
4. 先讲人人能听懂的故事、自然现象、生活常识或"像古代会发生的旧事"，再把它类比到用户原话，最后得出一本正经而略显牵强的结论。
5. 笑点来自"论证严密，结论荒唐"，不来自生僻字。若提到古代人物或旧事，要用白话讲清楚，不能只报名字。
6. 借鉴课本白话翻译的腔调：把省略的主语、关系和名分补出来，把抽象的"礼、义、信、本分"翻成现代人能感到的分寸、责任、体面与信用。
7. 借鉴《孟子》式比喻论证：先用鱼与熊掌、饭食、行路、器物、宴席这类具体东西说明取舍，再推出看似郑重的结论。
8. 借鉴《论语》式设问和判断：可以用"难道不也是……吗""这就可以说是……""如果……那么……"收束，但不要装作真实引用。
9. 学《论语》白话译文的起手：约六成输出可以用"我听闻""我曾听闻""我听说"来制造"子曰/曾子曰"被翻译后的熟悉感；其余约四成应从原话对象、判断、反问或眼前场景开篇，避免模板化。不要点名孔子、周公等真实人物。把"吾……"这类第一人称，翻成"我……"的现代自述。
10. 学"吾日三省吾身"的三问结构：连续追问"为别人是否尽心、与朋友是否守信、对自己是否有交代"。现代小事也可以写成三问，让读者想起课文腔。
11. 学《出师表》式劝谏：先说明局势"这实在是……的时候"，再分出"应该如何、不应该如何"，语气恳切而不卑不亢。
12. 学《桃花源记》式叙事：先沿着日常场景往前走，忽然发现一个不对劲的细节，再"豁然开朗"地推出礼法解释。
13. 借鉴《周礼》式职分感：把现代小事说成"谁掌何事、谁失其职、名分是否相称、交接是否有节"。例如快递、会议、群聊、加班，都可以被说成一种职分和礼数。
14. 严禁伪装成真实经典引用：不要写"圣人云""古人云""孔子说""周公曰""《周礼》所言""某经有云""有个贤人说过""贤者讲过"等句式；不要给虚构句子加书名号出处；不要把自己编的句子放进像经典原文一样的引号里。需要古风依据时，改写成"若按礼法来看""古人会觉得""我听闻有这样一个道理""我听说从前有个贤人，他遇到过一件事……"这类明显是讲故事的白话。
15. 绝对不要输出这些机械尾巴："这正是我担忧的啊""你好好想想其中的道理""你且想想""这其中的道理""仔细想想其中的道理"。这些词组一旦出现，就视为不合格。结尾要直接、有梗、有收束。
16. 保留用户原意和情绪，不要擅自改变立场。
17. 先判定发言主体，再动笔：若原话包含"我/我们/我的"等自述信号，输出应使用第一人称"我/我们"，不要改成"你/您/他"。若原话评价第三方，可用第三视角。
18. 遇到粗口/强情绪句时，任务不是劝发言者冷静，而是把同一份不满换成合乎周礼的表达。
19. 输出前先从以下四档辞气中选择最合适的一档：温言相劝（态度温和，先理解再劝说）/ 大儒辩经（貌似严谨的论证，含反例/反问）/ 强行圆场（为行为找礼法解释）/ 痛心疾首（把小事提升到礼法高度，郑重但幽默）。不需要标注选择了哪一档。
20. 输出长度控制在 150-220 字，一至两个例子，起承转合。不要超过 250 字。
21. 输出只包含改写结果，不解释创作过程，不加标题，不使用 Markdown。"""

ZHOULI_PRIMARY_API_KEY = os.getenv("DECISION_API_KEY", "")
ZHOULI_PRIMARY_URL = "https://api.llm.ustc.edu.cn/v1/chat/completions"
ZHOULI_PRIMARY_MODEL = "deepseek-v4-pro"
ZHOULI_FALLBACK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
ZHOULI_FALLBACK_URL = "https://api.deepseek.com/v1/chat/completions"
ZHOULI_FALLBACK_MODEL = "deepseek-chat"
ZHOULI_MAX_TOKENS = 300
ZHOULI_TIMEOUT = 30
ZHOULI_MAX_LENGTH = 300
ZHOULI_RATE_LIMIT = 3
ZHOULI_RATE_WINDOW = 3600
zhouli_usage: Dict[str, list] = defaultdict(list)

# NapCat HTTP API
NAPCAT_HTTP_URL = "http://127.0.0.1:6099"
VOICE_TRANSCRIBE_TIMEOUT = 10

# 像素画
PIXEL_SIZE = 32
PREVIEW_SCALE = 10
TEMP_IMAGE_PATH = os.path.join(SCRIPT_DIR, "temp_input.jpg")
TEMP_PREVIEW_PATH = os.path.join(SCRIPT_DIR, "preview.png")
TEMP_TEX_PATH = os.path.join(SCRIPT_DIR, "pixel_output.tex")

# Yau 频率限制记录
yau_usage: Dict[str, list] = defaultdict(list)

# 消息去重
processed_msg_ids: Set[int] = set()
PROCESSED_MSG_MAX = 200

# 运行时状态
known_groups: set = set()

# ========== 外部依赖（延迟导入） ==========
bot_logger = None
decision_engine = None
send_message_fn = None
send_group_image_fn = None
send_group_file_fn = None


def init_handlers(logger, de, send_msg_fn, send_img_fn, send_file_fn):
    """初始化外部引用（reverse_bot.py 在 main() 中调用一次）"""
    global bot_logger, decision_engine, send_message_fn, send_group_image_fn, send_group_file_fn
    bot_logger = logger
    decision_engine = de
    send_message_fn = send_msg_fn
    send_group_image_fn = send_img_fn
    send_group_file_fn = send_file_fn


# ========== 可选依赖 ==========
try:
    from zhdate import ZhDate
    HAS_ZHDATE = True
except ImportError:
    HAS_ZHDATE = False

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# 签到 / 神秘数字 / 图片模块
from scripts.sign_in import process_sign_in
from scripts.mystery_number import find_valid_number_async as find_valid_number
from scripts.nekosia_image import fetch_catgirl_image
from scripts.pixiv_helper import fetch_random_pixiv_image, download_pixiv_image

# ========== 数据操作 ==========
def load_data() -> Dict:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if bot_logger:
            bot_logger.info(f"[警告] 读取数据文件失败: {e}")
        return {}


def save_data(data: Dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_dish(group_id: str, user_id: str, dish: str) -> bool:
    data = load_data()
    group_data = data.setdefault(group_id, {})
    user_menu = group_data.setdefault(user_id, [])
    if dish in user_menu:
        return False
    user_menu.append(dish)
    save_data(data)
    return True


def remove_dish(group_id: str, user_id: str, dish: str) -> bool:
    data = load_data()
    group_data = data.get(group_id, {})
    user_menu = group_data.get(user_id, [])
    if dish not in user_menu:
        return False
    user_menu.remove(dish)
    if not user_menu:
        del group_data[user_id]
    if not group_data:
        del data[group_id]
    save_data(data)
    return True


def get_personal_menu(group_id: str, user_id: str) -> List[str]:
    data = load_data()
    return data.get(group_id, {}).get(user_id, [])


def get_all_dishes(group_id: str) -> List[tuple]:
    data = load_data()
    group_data = data.get(group_id, {})
    items = []
    for uid, dishes in group_data.items():
        for dish in dishes:
            items.append((dish, uid))
    return items


# ========== 加密/解密 ==========
def encrypt(plaintext: str, key: str = "") -> str:
    """将明文按咕嘎加密算法加密为密文字符串"""
    utf8_bytes = plaintext.encode("utf-8")
    result = []
    for byte in utf8_bytes:
        for i in range(7, -1, -1):
            bit = (byte >> i) & 1
            result.append("咕" if bit == 0 else "嘎")
    return "".join(result)


def decrypt(ciphertext: str, key: str = "") -> str:
    """将咕嘎密文按解密算法恢复为原始明文"""
    if not ciphertext:
        return ""
    bits = []
    for ch in ciphertext:
        if ch == "咕":
            bits.append(0)
        elif ch == "嘎":
            bits.append(1)
        else:
            raise ValueError("密文只能包含「咕」和「嘎」，请检查输入")
    if len(bits) % 8 != 0:
        raise ValueError("密文长度无效，解密失败")
    utf8_bytes = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        utf8_bytes.append(byte)
    try:
        return utf8_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("解密失败，密文可能已损坏")


# ========== 算卦/占卜数据 ==========
YIJING_DATA_FILE = os.path.join(SCRIPT_DIR, "yijing_structured_fixed.json")

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
        if bot_logger:
            bot_logger.info(f"[算卦] 加载易经数据失败: {e}")
        return None


def init_divination_data():
    """初始化卦表、阴阳数组、变卦映射"""
    global gua_table, gua_data, gua_yinyang, bian_gua_map
    hexagrams = load_yijing_data()
    if not hexagrams:
        if bot_logger:
            bot_logger.info("[算卦] 未加载到易经数据，算卦功能不可用")
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
    """调用 DeepSeek API 解读卦象（完全异步）"""
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

    timeout = httpx.Timeout(15)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": DIVINATION_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": DIVINATION_MAX_TOKENS,
            "temperature": 0.7,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DIVINATION_API_KEY}",
        }
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            json=payload, headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        if not content:
            if bot_logger:
                bot_logger.info("[占卜] 警告: AI 返回了空内容")
            return "卦象已出，但解读为空，请换个描述重试。"
        return content


# ========== Yau 相关 ==========
def load_yau_system_prompt() -> str:
    """尝试从外部文件加载风格指令，失败则使用内置精简版"""
    style_file = os.path.join(SCRIPT_DIR, "yau_style.txt")
    if os.path.exists(style_file):
        try:
            with open(style_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                if bot_logger:
                    bot_logger.info(f"[Yau] 从 {style_file} 加载风格指令（{len(content)} 字符）")
                return content
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[Yau] 读取风格文件失败: {e}")
    if bot_logger:
        bot_logger.info("[Yau] 使用内置精简版风格指令")
    return YAU_SYSTEM_PROMPT_BUILTIN


# 模块启动时加载 Yau 风格指令
yau_system_prompt = load_yau_system_prompt()


def check_yau_rate_limit(user_id: str) -> bool:
    """检查用户是否超过频率限制，成功则记录本次使用（需外部持 _yau_lock）"""
    now = time.time()
    usage = yau_usage[user_id]
    yau_usage[user_id] = [t for t in usage if now - t < YAU_WINDOW]
    if len(yau_usage[user_id]) >= YAU_MAX_USAGE:
        return False
    yau_usage[user_id].append(now)
    return True


async def call_deepseek(user_message: str, system_prompt: str, max_tokens: int = 150) -> str:
    """调用 DeepSeek API（完全异步，不占用线程池）"""
    timeout = httpx.Timeout(YAU_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message or "iyau"},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.8,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }
        resp = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            json=payload, headers=headers,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]


# ========== 周礼文案生成 ==========

def clean_zhouli_text(value: str) -> str:
    """清理 LLM 输出的 Markdown、伪经典引用、机械尾巴"""
    value = re.sub(r'(?:圣人|古人|孔子|周公)(?:云|曰|说)[，,：:]*', '若按礼法来看，', value)
    value = re.sub(r'《[^》]{1,12}》(?:所言|有云|曰|云|说|记载)[，,：:]*', '若按礼法来看，', value)
    value = re.sub(r'这正是我担忧的啊[，,。！？!?]*', '', value)
    value = re.sub(r'(?:你且想想|你好好想想|仔细想想)(?:其中的道理)?[，,、：:]*', '', value)
    value = re.sub(r'这其中的道理[，,、：:]*', '', value)
    value = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', value)
    value = re.sub(r'`([^`\n]+)`', r'\1', value)
    value = re.sub(r'^#{1,6}\s*', '', value, flags=re.MULTILINE)
    value = re.sub(r'^\s*[-*]\s+', '', value, flags=re.MULTILINE)
    value = re.sub(r'[ \t]+\n', '\n', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()


def check_zhouli_rate_limit(user_id: str) -> bool:
    """检查频率限制，需外部持 _zhouli_lock"""
    now = time.time()
    usage = zhouli_usage[user_id]
    zhouli_usage[user_id] = [t for t in usage if now - t < ZHOULI_RATE_WINDOW]
    if len(zhouli_usage[user_id]) >= ZHOULI_RATE_LIMIT:
        return False
    zhouli_usage[user_id].append(now)
    return True


async def call_zhouli_api(text: str, api_key: str, url: str, model: str) -> str:
    """调用单个 API 生成周礼文案"""
    timeout = httpx.Timeout(ZHOULI_TIMEOUT)
    async with httpx.AsyncClient(timeout=timeout) as client:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": ZHOULI_SYSTEM_PROMPT},
                {"role": "user", "content": f"请将下面这句话改写得合乎周礼：\n<原话>\n{text}\n</原话>"},
            ],
            "max_tokens": ZHOULI_MAX_TOKENS,
            "temperature": 0.9,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"]


async def call_zhouli(text: str) -> str:
    """调用周礼 API，优先中科大代理，失败后切 DeepSeek 官方"""
    # 尝试主模型
    try:
        if ZHOULI_PRIMARY_API_KEY:
            if bot_logger:
                bot_logger.info(f"[周礼] 调用主模型 ({ZHOULI_PRIMARY_MODEL})")
            raw = await call_zhouli_api(text, ZHOULI_PRIMARY_API_KEY, ZHOULI_PRIMARY_URL, ZHOULI_PRIMARY_MODEL)
            if raw and raw.strip():
                if bot_logger:
                    bot_logger.info(f"[周礼] 主模型成功")
                return clean_zhouli_text(raw)
            else:
                if bot_logger:
                    bot_logger.info("[周礼] 主模型返回空内容")
    except httpx.HTTPStatusError as e:
        if bot_logger:
            bot_logger.info(f"[周礼] 主模型 HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        if bot_logger:
            bot_logger.info(f"[周礼] 主模型异常: {e}")

    # 尝试备用模型
    try:
        if ZHOULI_FALLBACK_API_KEY:
            if bot_logger:
                bot_logger.info(f"[周礼] 切备用模型 ({ZHOULI_FALLBACK_MODEL})")
            raw = await call_zhouli_api(text, ZHOULI_FALLBACK_API_KEY, ZHOULI_FALLBACK_URL, ZHOULI_FALLBACK_MODEL)
            if raw and raw.strip():
                if bot_logger:
                    bot_logger.info(f"[周礼] 备用模型成功")
                return clean_zhouli_text(raw)
            else:
                if bot_logger:
                    bot_logger.info("[周礼] 备用模型返回空内容")
    except httpx.HTTPStatusError as e:
        if bot_logger:
            bot_logger.info(f"[周礼] 备用模型 HTTP {e.response.status_code}: {e.response.text[:200]}")
    except Exception as e:
        if bot_logger:
            bot_logger.info(f"[周礼] 备用模型异常: {e}")

    return ""


async def _classify_memory(text: str, char_name: str) -> Tuple[str, str]:
    """调用 LLM 将自然语言描述分类为记忆条目。"""
    system_prompt = (
        "你是一个记忆分类助手。将用户的自然语言描述转换为角色记忆条目。\n"
        "可用类别：identity(身份), relationships(关系), beliefs(信念), "
        "knowledge(知识), events(经历), preferences(偏好)\n"
        "输出 JSON 格式：{\"category\": \"类别名\", \"text\": \"精炼后的记忆语句\"}\n"
        "只输出 JSON，不要其他内容。"
    )
    try:
        content = await call_deepseek(text, system_prompt, max_tokens=100)
        content = content.strip()
        content = re.sub(r'^```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```$', '', content)
        data = json.loads(content)
        category = data.get("category", "")
        refined = data.get("text", "")
        valid_categories = {"identity", "relationships", "beliefs", "knowledge", "events", "preferences"}
        if category in valid_categories and refined:
            return (category, refined.strip())
        if bot_logger:
            bot_logger.info(f"[记忆分类] LLM 返回格式异常: {content[:100]}")
        return ("", "")
    except Exception as e:
        if bot_logger:
            bot_logger.info(f"[记忆分类] 调用失败: {e}")
        return ("", "")


async def test_model(name: str, api_key: str, base_url: str, model: str) -> str:
    """测试模型 API 连通性，返回原始响应详情"""
    t_start = time.time()
    payload = {
        "model": model, "max_tokens": 10,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    lines = [f"=== {name} ({model}) ===", f"POST {base_url}"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(base_url, json=payload, headers=headers)
            elapsed = time.time() - t_start
            lines.append(f"Status: {resp.status_code}  |  Time: {elapsed:.2f}s")

            useful_headers = [
                "content-type", "date", "server", "cf-ray",
                "x-request-id", "x-ratelimit-remaining", "x-ratelimit-limit",
                "x-ds-trace-id", "x-forwarded-for",
            ]
            header_lines = []
            for h in useful_headers:
                val = resp.headers.get(h)
                if val:
                    header_lines.append(f"  {h}: {val}")
            if header_lines:
                lines.append("Response Headers:")
                lines.extend(header_lines)

            raw_body = resp.text
            if len(raw_body) > 800:
                raw_body = raw_body[:800] + "\n... (truncated)"
            lines.append("Response Body:")
            lines.append(raw_body)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        lines.append(f"Reply: {content.strip()}")
                except Exception:
                    pass
                lines.append("Result: ✅ 连通成功")
            else:
                lines.append(f"Result: ❌ 连通失败 (HTTP {resp.status_code})")

    except httpx.TimeoutException as e:
        elapsed = time.time() - t_start
        lines.append(f"Status: TIMEOUT  |  Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败 (TimeoutException: {e})")
    except Exception as e:
        elapsed = time.time() - t_start
        lines.append(f"Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败 ({type(e).__name__}: {e})")

    return "\n".join(lines)


# ========== 像素画功能 ==========
async def download_image(url: str, save_path: str):
    """异步下载图片到本地文件"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                f.write(await resp.read())


def pixelate_and_create_tex(image_path: str, preview_path: str, tex_path: str):
    """将图片像素化并生成预览图和 LaTeX 表格文件"""
    img = Image.open(image_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    small_img = img.resize((PIXEL_SIZE, PIXEL_SIZE), Image.NEAREST)

    preview_img = small_img.resize(
        (PIXEL_SIZE * PREVIEW_SCALE, PIXEL_SIZE * PREVIEW_SCALE),
        Image.NEAREST,
    )
    preview_img.save(preview_path)

    lines = [
        "\\documentclass{article}",
        "\\usepackage[table]{xcolor}",
        "\\begin{document}",
        "\\resizebox{\\linewidth}{!}{%",
        "\\begin{tabular}{" + "*{" + str(PIXEL_SIZE) + "}{c}" + "}",
    ]
    for y in range(PIXEL_SIZE):
        row = []
        for x in range(PIXEL_SIZE):
            r, g, b = small_img.getpixel((x, y))
            row.append(
                f"\\cellcolor[rgb]{{{r/255:.2f},{g/255:.2f},{b/255:.2f}}}\\rule{{0.3em}}{{0.3em}}"
            )
        lines.append(" & ".join(row) + " \\\\")
    lines.extend(["\\end{tabular}%", "}", "\\end{document}"])

    with open(tex_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ========== SimPiano 钢琴功能 ==========
class SimPianoError(Exception):
    """自定义异常，包含错误位置和描述"""
    def __init__(self, pos, msg):
        self.pos = pos
        self.msg = msg
        super().__init__(f"位置 {pos}: {msg}")


def _note_to_midi(base, sharps, flats, octave_shift):
    """将音高参数转换为 MIDI 音符号"""
    semitone = [0, 2, 4, 5, 7, 9, 11]
    note = semitone[base - 1]
    note += sharps
    note -= flats
    return 60 + octave_shift * 12 + note


def _midi_to_freq(midi):
    return 440.0 * (2 ** ((midi - 69) / 12.0))


_NOTE_RE = re.compile(r'^(\**)([1-7])(\**)([#!]?)(~*)$')
_NOTE_RE_GROUP = re.compile(r'^(\**)([1-7])(\**)([#!]?)(~*)')
_TEMPO_RE = re.compile(r'^\[(\d+)\]$', re.ASCII)


def _parse_note(token, pos):
    """解析单个音符 token，返回 (midi, dur_beats)"""
    m = _NOTE_RE.match(token)
    if not m:
        raise SimPianoError(pos, f"无效的音符格式: '{token}'")
    pre_star, digit, post_star, accidental, tildes = m.groups()
    base = int(digit)
    octave = len(post_star) - len(pre_star)
    sharps = 1 if accidental == '#' else 0
    flats = 1 if accidental == '!' else 0
    midi = _note_to_midi(base, sharps, flats, octave)
    return midi, 1 + len(tildes)


def _parse_rest(token, pos):
    """解析休止符 token，返回持续时间（拍）"""
    if token[0] != '-':
        raise SimPianoError(pos, "内部错误：非休止符传入 parse_rest")
    if set(token) == {'-'}:
        return len(token)
    if not re.fullmatch(r'-~+', token):
        raise SimPianoError(pos, f"无效的休止符格式: '{token}'")
    return 1 + token.count('~')


def _parse_group(token, pos):
    """解析括号分组 token。"""
    if not (token.startswith('(') and token.endswith(')')):
        raise SimPianoError(pos, f"分组括号不匹配: '{token}'")
    inner = token[1:-1]
    if not inner:
        raise SimPianoError(pos, "空括号")

    extend_shares = 0
    while inner.startswith('~'):
        extend_shares += 1
        inner = inner[1:]

    elements = []
    i = 0
    while i < len(inner):
        if inner[i].isspace():
            i += 1
            continue
        sub = inner[i:]
        m = _NOTE_RE_GROUP.match(sub)
        if m:
            elements.append(('note', m.group(0)))
            i += len(m.group(0))
            continue
        m_rest = re.match(r'-~*', sub)
        if m_rest:
            elements.append(('rest', m_rest.group(0)))
            i += len(m_rest.group(0))
            continue
        raise SimPianoError(pos, f"括号内无效格式，位置 {i}")

    shares = [(1 + tok.count('~')) for _, tok in elements]

    if extend_shares > 0:
        raw_list = []
        for (typ, tok), share in zip(elements, shares):
            if typ == 'note':
                midi, _ = _parse_note(tok, pos)
                raw_list.append(('note', midi, share))
            else:
                raw_list.append(('rest', share))
        return ('extend', extend_shares, raw_list)

    total = sum(shares)
    sub_events = []
    for (typ, tok), share in zip(elements, shares):
        dur = share / total
        if typ == 'note':
            midi, _ = _parse_note(tok, pos)
            sub_events.append(('note', midi, dur))
        else:
            sub_events.append(('rest', dur))
    return sub_events, 1.0


def custom_split(text):
    """智能分词：识别括号组 (...) 和速度标记 [...] 为独立 token。"""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '(':
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                j += 1
            if depth != 0:
                raise SimPianoError(i, "括号未闭合")
            tokens.append(text[i:j])
            i = j
        elif ch == '[':
            j = i + 1
            while j < n and text[j] != ']':
                j += 1
            if j >= n:
                raise SimPianoError(i, "方括号未闭合")
            j += 1
            tokens.append(text[i:j])
            i = j
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()[]':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def tokenize_and_parse(text):
    """主解析函数：返回事件列表。"""
    text = html.unescape(text)
    tokens = custom_split(text)
    tokens = [t for t in tokens if t != '|']
    if not tokens:
        return []

    raw_events = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        token = token.strip()
        token = re.sub(r'[​‌‍⁠﻿]', '', token)

        m = _TEMPO_RE.match(token)
        if not m:
            m2 = re.match(r'^\[(\d+)\]$', token.strip())
            if m2:
                m = m2
        if m:
            bpm = int(m.group(1))
            if bpm <= 0:
                raise SimPianoError(i, f"速度必须是正整数，得到 {bpm}")
            raw_events.append(('tempo', bpm))
            i += 1
            continue

        if token.startswith('('):
            if not token.endswith(')'):
                raise SimPianoError(i, "括号未闭合")
            result = _parse_group(token, i)
            if isinstance(result, tuple) and len(result) == 3 and result[0] == 'extend':
                raw_events.append(('__extend__', result[1], result[2]))
            else:
                sub_events, _ = result
                raw_events.extend(sub_events)
            i += 1
            continue
        if token.endswith(')') and not token.startswith('('):
            raise SimPianoError(i, "意外的右括号")

        if token[0] == '-':
            raw_events.append(('rest', _parse_rest(token, i)))
            i += 1
            continue

        if token[0] in '*1234567':
            if not _NOTE_RE.match(token):
                raise SimPianoError(i, f"无效的音符格式: '{token}'")
            midi, dur = _parse_note(token, i)
            raw_events.append(('note', midi, dur))
            i += 1
            continue

        raise SimPianoError(i, f"无法识别的符号: '{token}'")

    events = []
    for ev in raw_events:
        if ev[0] == '__extend__':
            _, extra, sub_events = ev
            if not events:
                raise SimPianoError(0, "延长括号前没有音符")
            last_idx = len(events) - 1
            while last_idx >= 0 and events[last_idx][0] == 'tempo':
                last_idx -= 1
            if last_idx < 0:
                raise SimPianoError(0, "延长括号前没有音符")

            last_ev = events.pop(last_idx)

            if last_ev[0] == 'note':
                _, last_midi, last_dur = last_ev
                last_share = last_dur
            elif last_ev[0] == 'rest':
                _, last_dur = last_ev
                last_share = last_dur
            else:
                raise SimPianoError(0, f"延长括号前不支持的事件类型: {last_ev[0]}")

            sub_shares = []
            for sub_ev in sub_events:
                if sub_ev[0] == 'note':
                    sub_shares.append(sub_ev[2])
                else:
                    sub_shares.append(sub_ev[1])

            extra_beats = extra
            prev_weight = extra
            total_weight = prev_weight + sum(sub_shares)

            prev_extra = extra_beats * prev_weight / total_weight
            prev_total_dur = last_share + prev_extra

            new_events = []
            if last_ev[0] == 'note':
                new_events.append(('note', last_midi, prev_total_dur))
            else:
                new_events.append(('rest', prev_total_dur))

            for sub_ev, share in zip(sub_events, sub_shares):
                sub_dur = extra_beats * share / total_weight
                if sub_ev[0] == 'note':
                    new_events.append(('note', sub_ev[1], sub_dur))
                else:
                    new_events.append(('rest', sub_dur))

            events[last_idx:last_idx] = new_events
        else:
            events.append(ev)

    if not any(ev[0] == 'tempo' for ev in events):
        events.insert(0, ('tempo', 120))
    return events


_SAMPLE_RATE = 44100


def _generate_piano_wave(midi, duration_sec, sample_rate=_SAMPLE_RATE):
    """生成钢琴音色波形（基频 + 泛音 + 指数衰减包络）"""
    freq = _midi_to_freq(midi)
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    harmonics = [1.0, 0.5, 0.25, 0.125, 0.06]
    wave = np.zeros_like(t)
    for k, weight in enumerate(harmonics, 1):
        wave += weight * np.sin(2 * np.pi * k * freq * t)
    env = np.ones_like(t)
    attack_samples = int(0.01 * sample_rate)
    if attack_samples > 0 and len(env) > attack_samples:
        env[:attack_samples] = np.linspace(0, 1, attack_samples)
    decay = duration_sec * 0.4
    if decay > 0:
        env = env * np.exp(-t / decay)
    wave *= env
    max_abs = np.max(np.abs(wave))
    if max_abs > 0:
        wave = wave / max_abs
    return wave


def events_to_audio(events, sample_rate=_SAMPLE_RATE):
    """将事件列表转换为音频波形数组，返回 (audio, sample_rate)"""
    abs_events = []
    current_time = 0.0
    current_bpm = 120
    for ev in events:
        if ev[0] == 'tempo':
            current_bpm = ev[1]
        else:
            beat_sec = 60.0 / current_bpm
            if ev[0] == 'note':
                dur_sec = ev[2] * beat_sec
                abs_events.append(('note', current_time, ev[1], dur_sec))
                current_time += dur_sec
            elif ev[0] == 'rest':
                current_time += ev[1] * beat_sec
    if not abs_events:
        return np.zeros(int(0.5 * sample_rate)), sample_rate
    total = max(s + d for _, s, _, d in abs_events) + 0.1
    audio = np.zeros(int(total * sample_rate))
    for _, start, midi, dur in abs_events:
        w = _generate_piano_wave(midi, dur, sample_rate)
        si = int(start * sample_rate)
        ei = si + len(w)
        if ei > len(audio):
            audio = np.pad(audio, (0, ei - len(audio)))
        audio[si:ei] += w
    max_v = np.max(np.abs(audio))
    if max_v > 1.0:
        audio = audio / max_v * 0.9
    return audio, sample_rate


def write_wav(filename, audio, sample_rate):
    """将 numpy 数组写入 16-bit PCM WAV 文件"""
    import wave as _wave
    audio_int16 = np.int16(audio * 32767)
    with _wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())


def _piano_synthesize(events, out_path):
    """合成音频并写入 WAV 文件（供线程池调用）"""
    audio, sr = events_to_audio(events)
    write_wav(out_path, audio, sr)


# ========== 语音消息转文字 ==========
_RECORD_CQ_RE = re.compile(r'\[CQ:record,file=([^\]]*)\]')


async def _transcribe_voice(file_param: str) -> Optional[str]:
    """调用 NapCat HTTP API 将语音转写为文字。成功返回文本，失败返回 None。"""
    try:
        file_id = urllib.parse.unquote(file_param)
        async with httpx.AsyncClient(timeout=VOICE_TRANSCRIBE_TIMEOUT) as client:
            resp = await client.post(
                f"{NAPCAT_HTTP_URL}/fetch_ptt_text",
                json={"file_id": file_id},
                headers={"Content-Type": "application/json"},
            )
            result = resp.json()
            text = result.get("data", {}).get("text", "")
            return text.strip() if text else None
    except httpx.TimeoutException:
        if bot_logger:
            bot_logger.info(f"[语音转写] API 超时: file={file_param[:40]}")
    except Exception as e:
        if bot_logger:
            bot_logger.info(f"[语音转写] API 调用失败: {e} (file={file_param[:40]})")
    return None


async def process_voice_message(raw_message: str) -> str:
    """将消息中的 [CQ:record,file=...] 替换为转写文字。"""
    matches = list(_RECORD_CQ_RE.finditer(raw_message))
    if not matches:
        return raw_message

    text = raw_message
    for m in reversed(matches):
        file_param = m.group(1)
        if bot_logger:
            bot_logger.info(f"[语音转写] 收到语音消息，file={file_param[:60]}")

        result = await _transcribe_voice(file_param)
        if result:
            if bot_logger:
                bot_logger.info(f"[语音转写] 转写成功: {result[:60]}")
            text = text[:m.start()] + result + text[m.end():]
        else:
            if bot_logger:
                bot_logger.info(f"[语音转写] 转写失败，保留原 CQ 码")

    return text


# ========== 帮助系统 ==========
COMMANDS = [
    ("查看菜单 / 我的菜单", "查看你的所有菜品"),
    ("添加菜单 <菜名> / 加菜 <菜名>", "添加菜品到你的菜单"),
    ("删除菜单 <菜名> / 删菜 <菜名>", "删除你菜单中的菜品"),
    ("今天吃啥 [自己|群]", "从自己的菜单中随机选一个（不加参数默认从全群选）"),
    ("选择 <选项1> <选项2> ...", "从多个选项中随机选一个"),
    ("Yau <你想说的话>", "让丘成桐先生帮你指点一二（每小时限3次）"),
    ("加密 <文字>", "将文字加密成一串咕嘎密文"),
    ("解密 <咕嘎密文>", "将咕嘎密文解密回原始文字"),
    ("算卦 / 占卜 [事件描述]", "根据易经起卦占卜，并解读吉凶"),
    ("像素画", "（需同时发送图片）将图片转为像素风格并生成 LaTeX 表格文件"),
    ("钢琴 <SimPiano曲谱>", "将曲谱转换为钢琴音频文件（WAV）发送"),
    ("test", "测试主模型和备用模型的 API 连通性"),
    ("添加记忆 <自然语言>", "向当前群添加角色记忆（需启用角色）"),
    ("删除记忆 <关键词>", "删除包含关键词的群记忆（管理员可用）"),
    ("查看记忆", "查看当前群的角色记忆文件（发送文件）"),
    ("切换角色 <角色名>", "切换到指定角色（管理员可用）"),
    ("角色列表", "列出所有可用角色"),
    ("启用角色", "启用角色模式（管理员可用）"),
    ("停用角色", "停用角色模式，回退默认人设（管理员可用）"),
    ("帮助 / help", "显示本帮助"),
]

HELP_CATEGORIES = {
    "菜单": {
        "title": "菜单管理",
        "commands": [
            ("查看菜单 / 我的菜单", "查看你的所有菜品"),
            ("添加菜单 <菜名> / 加菜 <菜名>", "添加菜品到你的菜单"),
            ("删除菜单 <菜名> / 删菜 <菜名>", "删除你菜单中的菜品"),
            ("今天吃啥 [自己|群]", "从菜单中随机选一个（不加参数默认从全群选）"),
        ],
    },
    "小巧思": {
        "title": "娱乐小工具",
        "commands": [
            ("选择 <选项1> <选项2> ...", "从多个选项中随机选一个"),
            ("Yau <你想说的话>", "让丘成桐先生帮你指点一二（每小时限3次）"),
            ("周礼 <文字>", "将文字改写成合乎周礼的白话翻译腔"),
            ("加密 <文字>", "将文字加密成一串咕嘎密文"),
            ("解密 <咕嘎密文>", "将咕嘎密文解密回原始文字"),
            ("算卦 / 占卜 [事件描述]", "根据易经起卦占卜，并解读吉凶"),
            ("猫娘", "随机获取一张可爱的猫娘图片"),
            ("pixiv随机日榜", "随机获取 Pixiv 日榜前十的一张图片"),
            ("pixiv随机周榜", "随机获取 Pixiv 周榜前十五的一张图片"),
            ("pixiv随机月榜", "随机获取 Pixiv 月榜前二十的一张图片"),
            ("像素画", "（需同时发送图片）将图片转为像素风格并生成 LaTeX 表格文件"),
            ("钢琴 <SimPiano曲谱>", "将曲谱转换为钢琴音频文件（WAV）发送"),
        ],
    },
    "角色": {
        "title": "角色与记忆管理",
        "commands": [
            ("添加记忆 <自然语言>", "向当前角色添加记忆（需启用角色）"),
            ("删除记忆 <关键词>", "删除包含关键词的记忆（管理员可用）"),
            ("查看记忆", "列出当前角色的所有记忆"),
            ("切换角色 <角色名>", "切换到指定角色（管理员可用）"),
            ("角色列表", "列出所有可用角色"),
            ("启用角色", "启用角色模式（管理员可用）"),
            ("停用角色", "停用角色模式，回退默认人设（管理员可用）"),
        ],
    },
    "系统": {
        "title": "系统工具",
        "commands": [
            ("切换模型 模型名 / 切模型 模型名", "切换决策引擎主模型（所有人可用）"),
            ("查看模型 / 模型列表", "列出可用模型及当前主模型"),
            ("test", "测试主模型和备用模型的 API 连通性"),
        ],
    },
    "日常": {
        "title": "日常功能",
        "commands": [
            ("签到", "每日签到，获取神秘数字"),
            ("神秘数字", "随机召唤一个神秘数字"),
        ],
    },
}

HELP_SHORT = {
    "菜单": "菜单管理",
    "小巧思": "娱乐小工具",
    "角色": "角色与记忆管理",
    "系统": "系统工具",
    "日常": "日常功能",
}


def _is_command(msg):
    """判断是否为已知指令"""
    msg = msg.strip()
    if not msg:
        return False
    if msg.startswith("Yau"):
        return True
    if msg.startswith("周礼"):
        return True
    if "像素画" in msg:
        return True
    if msg.startswith("加密") or msg.startswith("解密"):
        return True
    if msg.startswith("算卦") or msg.startswith("占卜"):
        return True
    if msg.startswith("钢琴"):
        return True
    if re.match(r"^(添加菜单|加菜)\s+", msg):
        return True
    if re.match(r"^(删除菜单|删菜|删除)\s+", msg):
        return True
    if msg == "test":
        return True
    if msg in ("查看菜单", "我的菜单", "帮助", "help",
               "今天吃啥", "今天吃啥 群", "群里吃啥",
               "今天吃啥 自己", "我自己吃啥"):
        return True
    if re.match(r"^选择\s+", msg):
        return True
    if msg.startswith("添加记忆") or msg.startswith("删除记忆"):
        return True
    if msg in ("查看记忆", "角色列表", "启用角色", "停用角色"):
        return True
    if msg.startswith("切换角色"):
        return True
    if msg.startswith("切换模型") or msg.startswith("切模型"):
        return True
    if msg in ("查看模型", "模型列表"):
        return True
    if msg == "猫娘":
        return True
    if msg in ("pixiv随机日榜", "pixiv随机周榜", "pixiv随机月榜"):
        return True
    if msg == "神秘数字":
        return True
    if msg == "签到":
        return True
    for key in HELP_CATEGORIES:
        if msg == f"{key}帮助":
            return True
    return False


# ========== handle_command 主函数 ==========
async def handle_command(websocket, data: dict, clean_message: str, raw_message: str):
    """处理 @bot 指令。返回 True 表示已处理（指令匹配），False 表示非指令。"""
    group_id = data["group_id"]
    user_id = data["user_id"]
    uid_str = str(user_id)
    sender = data.get("sender", {})
    nickname = sender.get("card") or sender.get("nickname") or "用户"

    # 管理员检查
    def _is_admin(uid: str) -> bool:
        if not decision_engine:
            return False
        cfg = decision_engine.get_active_character()
        return uid in cfg.get("admin_ids", [])

    # ===== Yau 命令 =====
    if clean_message.startswith("Yau"):
        rest = clean_message[3:].strip()
        async with _yau_lock:
            if not check_yau_rate_limit(uid_str):
                await send_message_fn(websocket, group_id,
                    "您已达到每小时使用次数上限（3次），请稍后再试。", at_user=uid_str)
                return True
        try:
            prompt = rest if rest else "iyau"
            reply = await call_deepseek(prompt, yau_system_prompt, max_tokens=YAU_MAX_TOKENS)
        except (urllib.error.HTTPError, httpx.HTTPStatusError) as e:
            code = getattr(e, 'code', e.response.status_code if hasattr(e, 'response') else '?')
            if bot_logger:
                bot_logger.info(f"[Yau] API HTTP 错误: {code}")
            reply = "AI 暂时罢工了，可能是接口超时或配额不足，请稍后重试。"
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[Yau] API 调用失败: {e}")
            reply = "AI 暂时罢工了，请稍后重试。"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 周礼 =====
    if clean_message.startswith("周礼"):
        rest = clean_message[2:].strip()
        if not rest:
            await send_message_fn(websocket, group_id,
                "请提供需要改写的话，格式：周礼 <文字>", at_user=uid_str)
            return True
        if len(rest) > ZHOULI_MAX_LENGTH:
            await send_message_fn(websocket, group_id,
                f"文字太长，请控制在{ZHOULI_MAX_LENGTH}字以内", at_user=uid_str)
            return True
        async with _zhouli_lock:
            if not check_zhouli_rate_limit(uid_str):
                await send_message_fn(websocket, group_id,
                    "您已达到每小时使用次数上限（3次），请稍后再试。", at_user=uid_str)
                return True
        if bot_logger:
            bot_logger.info(f"[周礼] 开始处理: {rest[:30]}...")
        try:
            await send_message_fn(websocket, group_id,
                "礼官正在斟酌，请稍候~", at_user=uid_str)
            reply = await call_zhouli(rest)
            if reply:
                if bot_logger:
                    bot_logger.info(f"[周礼] 完成，{len(reply)}字")
            else:
                reply = "礼官远行未归，请稍后再试。"
                if bot_logger:
                    bot_logger.info("[周礼] 双模型均失败")
            await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[周礼] 处理异常: {e}")
                traceback.print_exc()
            await send_message_fn(websocket, group_id,
                "礼官远行未归，请稍后再试。", at_user=uid_str)
        return True

    # ===== 像素画 =====
    if "像素画" in clean_message:
        if not HAS_PIL or not HAS_AIOHTTP:
            await send_message_fn(websocket, group_id,
                "像素画功能缺少依赖，请联系管理员安装 Pillow 和 aiohttp", at_user=uid_str)
            return True
        image_url = None
        msg_segments = data.get("message", [])
        if isinstance(msg_segments, list):
            for seg in msg_segments:
                if isinstance(seg, dict) and seg.get("type") == "image":
                    image_url = seg.get("data", {}).get("url")
                    break
        if not image_url:
            await send_message_fn(websocket, group_id,
                '请发送一张图片并用"像素画"指令', at_user=uid_str)
            return True
        try:
            await send_message_fn(websocket, group_id, "正在处理像素画，请稍候~", at_user=uid_str)
            await download_image(image_url, TEMP_IMAGE_PATH)
            pixelate_and_create_tex(TEMP_IMAGE_PATH, TEMP_PREVIEW_PATH, TEMP_TEX_PATH)
            await send_group_image_fn(websocket, group_id, TEMP_PREVIEW_PATH)
            await send_group_file_fn(websocket, group_id, TEMP_TEX_PATH)
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[像素画] 处理失败: {e}")
            await send_message_fn(websocket, group_id,
                "像素画生成失败，请检查图片格式或稍后再试", at_user=uid_str)
        finally:
            for p in [TEMP_IMAGE_PATH, TEMP_PREVIEW_PATH, TEMP_TEX_PATH]:
                if os.path.exists(p):
                    os.remove(p)
        return True

    # ===== 加密命令 =====
    if clean_message.startswith("加密"):
        rest = clean_message[2:].strip()
        if not rest:
            await send_message_fn(websocket, group_id, "请提供需要加密的文字", at_user=uid_str)
            return True
        try:
            cipher = encrypt(rest)
            await send_message_fn(websocket, group_id, cipher, at_user=uid_str)
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[加密] 加密失败: {e}")
            await send_message_fn(websocket, group_id, "加密失败，请检查文字是否包含异常字符", at_user=uid_str)
        return True

    # ===== 解密命令 =====
    if clean_message.startswith("解密"):
        rest = clean_message[2:].strip()
        if not rest:
            await send_message_fn(websocket, group_id, "请提供需要解密的咕嘎密文", at_user=uid_str)
            return True
        try:
            plain = decrypt(rest)
            await send_message_fn(websocket, group_id, f"解密结果：{plain}", at_user=uid_str)
        except ValueError as e:
            await send_message_fn(websocket, group_id, str(e), at_user=uid_str)
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[解密] 解密失败: {e}")
            await send_message_fn(websocket, group_id, "解密失败，请检查文字是否包含异常字符", at_user=uid_str)
        return True

    # ===== 算卦/占卜 =====
    if clean_message.startswith("算卦") or clean_message.startswith("占卜"):
        event_text = re.sub(r"^(算卦|占卜)\s*", "", clean_message).strip()
        raw_event = event_text
        event_text = re.sub(r'[​‌‍⁠﻿ ]', '', event_text).strip()
        if not event_text:
            event_text = "一般性询问"
        if bot_logger:
            bot_logger.info(f"[占卜] 原始输入: {{{raw_event}}}, 清理后: {{{event_text}}}")

        if not _div_init_ok:
            await send_message_fn(websocket, group_id, "算卦数据未加载，请联系管理员。", at_user=uid_str)
            return True

        try:
            from datetime import datetime
            div_result = await asyncio.get_event_loop().run_in_executor(
                None, perform_divination, nickname, uid_str, event_text, None)
            if div_result["ben_gua"] == "未知":
                await send_message_fn(websocket, group_id, "算卦数据异常，请联系管理员。", at_user=uid_str)
                return True
            if bot_logger:
                bot_logger.info(f"[占卜] 调用 AI，事件描述: {event_text[:50]}")
            interpretation = await call_divination_api(div_result, event_text)
            if not interpretation or not interpretation.strip():
                if bot_logger:
                    bot_logger.info("[占卜] 警告: interpretation 为空，使用回退消息")
                interpretation = (
                    f"本卦：{div_result['ben_gua']}，动爻：{div_result['dong_yao']}\n"
                    f"体用：{div_result['sheng_ke']}，{div_result['ji_xiong']}")
            await send_message_fn(websocket, group_id, interpretation, at_user=uid_str)
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[算卦] 失败: {e}")
                traceback.print_exc()
            try:
                from datetime import datetime
                fallback = await asyncio.get_event_loop().run_in_executor(
                    None, perform_divination, nickname, uid_str, event_text, None)
                fallback_msg = (
                    f"卦象已出，但解读服务暂时不可用。\n"
                    f"本卦：{fallback['ben_gua']}，动爻：{fallback['dong_yao']}\n"
                    f"体用：{fallback['sheng_ke']}，{fallback['ji_xiong']}")
                await send_message_fn(websocket, group_id, fallback_msg, at_user=uid_str)
            except Exception as e2:
                if bot_logger:
                    bot_logger.info(f"[算卦] 回退也失败: {e2}")
                    traceback.print_exc()
                await send_message_fn(websocket, group_id, "占卜功能暂时不可用，请稍后再试。", at_user=uid_str)
        return True

    # ===== 猫娘 =====
    if clean_message == "猫娘":
        if bot_logger:
            bot_logger.info(f"[猫娘] 群{group_id} 用户{user_id} 请求猫娘图片")
        image_url = await fetch_catgirl_image()
        if not image_url:
            if bot_logger:
                bot_logger.info(f"[猫娘] 获取图片失败")
            await send_message_fn(websocket, group_id, "暂时获取不到猫娘图片，稍后再试试~", at_user=uid_str)
            return True
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": [
                    {"type": "at", "data": {"qq": uid_str}},
                    {"type": "image", "data": {"file": image_url}}
                ]
            }
        }
        try:
            await websocket.send(json.dumps(payload))
        except Exception:
            if bot_logger:
                bot_logger.warning("  ⚠️  发送猫娘图片失败: 连接已断开，消息已丢弃")
        return True

    # ===== Pixiv 排行榜 =====
    pixiv_rank_map = {
        "pixiv随机日榜": "daily",
        "pixiv随机周榜": "weekly",
        "pixiv随机月榜": "monthly",
    }
    if clean_message in pixiv_rank_map:
        rank_type = pixiv_rank_map[clean_message]
        if bot_logger:
            bot_logger.info(f"[Pixiv] 群{group_id} 用户{user_id} 请求 {clean_message}")
        result = await fetch_random_pixiv_image(rank_type)
        if not result:
            if bot_logger:
                bot_logger.info(f"[Pixiv] 获取 {clean_message} 图片失败")
            await send_message_fn(websocket, group_id,
                f"暂时获取不到{clean_message}的图片，稍后再试试~", at_user=uid_str)
            return True
        img_bytes = await download_pixiv_image(result["image_url"])
        if not img_bytes:
            if bot_logger:
                bot_logger.info(f"[Pixiv] 下载 {clean_message} 图片失败")
            await send_message_fn(websocket, group_id,
                f"暂时获取不到{clean_message}的图片，稍后再试试~", at_user=uid_str)
            return True
        b64_data = base64.b64encode(img_bytes).decode()
        full_url = result['rank_list_url']
        payload = {
            "action": "send_group_msg",
            "params": {
                "group_id": group_id,
                "message": [
                    {"type": "at", "data": {"qq": uid_str}},
                    {"type": "image", "data": {"file": f"base64://{b64_data}"}},
                    {"type": "text", "data": {"text": f"\n完整榜单请访问：{full_url}"}},
                ]
            }
        }
        try:
            await websocket.send(json.dumps(payload))
            if bot_logger:
                bot_logger.info(f"[Pixiv] 已推送 {clean_message}: {result['title']} (ID: {result['artwork_id']})")
        except Exception:
            if bot_logger:
                bot_logger.warning(f"  ⚠️  发送 {clean_message} 图片失败: 连接已断开，消息已丢弃")
        return True

    # ===== 钢琴 =====
    if clean_message.startswith("钢琴"):
        rest = clean_message[2:].strip()
        if rest in ("帮助", "help"):
            help_path = os.path.join(SCRIPT_DIR, "SimPiano.md")
            try:
                with open(help_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:
                if bot_logger:
                    bot_logger.info(f"[钢琴] 读取编码标准文件失败: {e}")
                content = "无法读取编码标准文件，请联系管理员"
            await send_message_fn(websocket, group_id, content, at_user=uid_str)
            return True

        if not HAS_NUMPY:
            await send_message_fn(websocket, group_id,
                "钢琴功能缺少依赖，请联系管理员安装 numpy（pip install numpy）", at_user=uid_str)
            return True
        score = rest
        if not score:
            await send_message_fn(websocket, group_id,
                "请提供 SimPiano 曲谱，示例：[80] 1 1 5 5 | 6 6 5~", at_user=uid_str)
            return True
        out_path = None
        try:
            events = tokenize_and_parse(score)
            if bot_logger:
                bot_logger.info(f"[钢琴] 解析成功，{len(events)} 个事件，来自 {user_id}")
            out_path = os.path.join(SCRIPT_DIR, f"piano_{int(time.time())}.wav")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: _piano_synthesize(events, out_path))
            await send_group_file_fn(websocket, group_id, out_path)
            if bot_logger:
                bot_logger.info(f"[钢琴] 已发送 {os.path.basename(out_path)}")
        except SimPianoError as e:
            await send_message_fn(websocket, group_id, f"曲谱解析失败：{e.msg}", at_user=uid_str)
            return True
        except Exception as e:
            if bot_logger:
                bot_logger.info(f"[钢琴] 合成失败: {e}")
                traceback.print_exc()
            await send_message_fn(websocket, group_id,
                "钢琴合成失败，请检查曲谱格式或联系管理员", at_user=uid_str)
            return True
        finally:
            if out_path and os.path.exists(out_path):
                os.remove(out_path)
        return True

    # ===== 添加菜单 =====
    match_add = re.match(r"^(添加菜单|加菜)\s+(.+)$", clean_message)
    if match_add:
        dish = match_add.group(2).strip()
        async with _menu_lock:
            if add_dish(str(group_id), uid_str, dish):
                reply = f"成功添加「{dish}」到你的菜单~"
            else:
                reply = f"你已经添加过「{dish}」了！"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 删除菜单 =====
    match_del = re.match(r"^(删除菜单|删菜|删除)\s+(.+)$", clean_message)
    if match_del:
        dish = match_del.group(2).strip()
        async with _menu_lock:
            if remove_dish(str(group_id), uid_str, dish):
                reply = f"已删除「{dish}」~"
            else:
                reply = f"你的菜单中没有「{dish}」，无法删除"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 查看菜单 =====
    if clean_message in ("查看菜单", "我的菜单"):
        menu = get_personal_menu(str(group_id), uid_str)
        if not menu:
            reply = "你还没有添加任何菜品，先输入「添加菜单 菜名」吧~"
        else:
            dish_list = "、".join(menu)
            reply = f"你的菜单（共 {len(menu)} 项）：{dish_list}"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 选择 =====
    match_select = re.match(r"^选择\s+(.+)$", clean_message)
    if match_select:
        options = match_select.group(1).strip().split()
        if len(options) <= 1:
            reply = "你让我选了吗？"
        else:
            chosen = random.choice(options)
            reply = f"我选：{chosen}"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== test =====
    if clean_message == "test":
        await send_message_fn(websocket, group_id, "正在测试模型连通性，请稍候...", at_user=uid_str)
        active_cfg = decision_engine.get_active_model()
        primary_result = await test_model(f"主模型 ({active_cfg['model']})",
            active_cfg["api_key"], active_cfg["api_base_url"], active_cfg["model"])
        fallback_result = await test_model("备用模型 (deepseek-chat)", DEEPSEEK_API_KEY,
            "https://api.deepseek.com/v1/chat/completions", "deepseek-chat")
        reply = f"模型连通性测试报告：\n\n{primary_result}\n\n{fallback_result}"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 神秘数字 =====
    if clean_message == "神秘数字":
        await _mystery_queue.put((websocket, group_id, uid_str))
        await send_message_fn(websocket, group_id, "神秘数字召唤中，请稍候...", at_user=uid_str)
        return True

    # ===== 签到 =====
    if clean_message == "签到":
        async with _sign_in_lock:
            result = process_sign_in(str(group_id), uid_str)
            if result is None:
                await send_message_fn(websocket, group_id,
                    "今天你已经签到过了哦~", at_user=uid_str)
                return True
            today_order = result["today_order"]
            total_days = result["total_days"]
            streak = result["streak"]
            rank = result["rank"]
            reply = (
                f"签到成功！你是今日第{today_order}位签到者，"
                f"累计签到{total_days}天，连续签到{streak}天，"
                f"全群排名第{rank}位。")
            await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 添加记忆（写入群专属记忆） =====
    if clean_message.startswith("添加记忆"):
        rest = clean_message[4:].strip()
        if not rest:
            await send_message_fn(websocket, group_id, "请提供要添加的记忆内容", at_user=uid_str)
            return True
        active_cfg = decision_engine.get_active_character()
        if not active_cfg.get("enabled"):
            await send_message_fn(websocket, group_id, "角色模式未启用，请先启用角色", at_user=uid_str)
            return True
        gid_str = str(group_id)
        char_name = decision_engine.get_character_for_group(gid_str)
        if not char_name:
            await send_message_fn(websocket, group_id, "当前群未配置角色，请先启用角色", at_user=uid_str)
            return True
        source = "admin" if _is_admin(uid_str) else "user"
        category, refined_text = await _classify_memory(rest, char_name)
        if not category or not refined_text:
            await send_message_fn(websocket, group_id, "记忆分类失败，请重试", at_user=uid_str)
            return True
        # 写入群专属记忆
        memories = decision_engine.load_group_memories(char_name, gid_str)
        if category not in memories:
            memories[category] = []
        for existing in memories[category]:
            if isinstance(existing, dict) and existing.get("text") == refined_text:
                await send_message_fn(websocket, group_id, f"这条记忆已经存在了", at_user=uid_str)
                return True
        memories[category].append({"text": refined_text, "source": source, "by": uid_str})
        decision_engine.save_group_memories(char_name, gid_str, memories)
        cat_label = {"identity": "身份", "relationships": "关系", "beliefs": "信念",
                     "knowledge": "知识", "events": "经历", "preferences": "偏好"}.get(category, category)
        await send_message_fn(websocket, group_id, f"已添加群记忆 [{cat_label}]：{refined_text}", at_user=uid_str)
        return True

    # ===== 删除记忆（删除群专属记忆） =====
    if clean_message.startswith("删除记忆"):
        rest = clean_message[4:].strip()
        if not rest:
            await send_message_fn(websocket, group_id, "请提供删除记忆的关键词", at_user=uid_str)
            return True
        active_cfg = decision_engine.get_active_character()
        if not active_cfg.get("enabled"):
            await send_message_fn(websocket, group_id, "角色模式未启用", at_user=uid_str)
            return True
        gid_str = str(group_id)
        char_name = decision_engine.get_character_for_group(gid_str)
        if not char_name:
            await send_message_fn(websocket, group_id, "当前群未配置角色", at_user=uid_str)
            return True
        # 操作群专属记忆
        memories = decision_engine.load_group_memories(char_name, gid_str)
        deleted = 0
        for cat in list(memories.keys()):
            original_count = len(memories[cat])
            memories[cat] = [
                item for item in memories[cat]
                if rest not in (item.get("text") if isinstance(item, dict) else item)
                or not (_is_admin(uid_str) or (isinstance(item, dict) and item.get("by") == uid_str))
            ]
            deleted += original_count - len(memories[cat])
        decision_engine.save_group_memories(char_name, gid_str, memories)
        await send_message_fn(websocket, group_id, f"已删除 {deleted} 条匹配的群记忆", at_user=uid_str)
        return True

    # ===== 查看记忆（角色记忆 + 群记忆） =====
    if clean_message == "查看记忆":
        active_cfg = decision_engine.get_active_character()
        if not active_cfg.get("enabled"):
            await send_message_fn(websocket, group_id, "角色模式未启用", at_user=uid_str)
            return True
        gid_str = str(group_id)
        char_name = decision_engine.get_character_for_group(gid_str)
        if not char_name:
            await send_message_fn(websocket, group_id, "当前群未配置角色", at_user=uid_str)
            return True
        char_memories = decision_engine.load_memories(char_name)
        group_memories = decision_engine.load_group_memories(char_name, gid_str)
        cat_labels = {
            "identity": "身份", "relationships": "关系", "beliefs": "信念",
            "knowledge": "知识", "events": "经历", "preferences": "偏好",
        }
        char_info = decision_engine.get_character_info(char_name)
        char_display = char_info.get("name", char_name) if char_info else char_name

        # 构建文件内容
        import io
        output = io.StringIO()
        output.write(f"角色: {char_display}\n")
        output.write(f"群: {gid_str}\n")
        output.write("=" * 40 + "\n\n")

        has_any = False
        # 角色通用记忆
        output.write("========== 角色通用记忆 ==========\n")
        if not char_memories:
            output.write("（无）\n")
        else:
            has_any = True
            for cat, label in cat_labels.items():
                items = char_memories.get(cat, [])
                if not items:
                    continue
                output.write(f"【{label}】\n")
                for item in items:
                    if isinstance(item, dict):
                        src = "⭐" if item.get("source") == "admin" else ""
                        output.write(f"{src}- {item.get('text', '')}\n")
                    elif isinstance(item, str):
                        output.write(f"- {item}\n")
                output.write("\n")

        # 群专属记忆
        output.write("========== 群专属记忆 ==========\n")
        if not group_memories:
            output.write("（无）\n")
        else:
            has_any = True
            for cat, label in cat_labels.items():
                items = group_memories.get(cat, [])
                if not items:
                    continue
                output.write(f"【{label}】\n")
                for item in items:
                    if isinstance(item, dict):
                        src = "⭐" if item.get("source") == "admin" else ""
                        output.write(f"{src}- {item.get('text', '')}\n")
                    elif isinstance(item, str):
                        output.write(f"- {item}\n")
                output.write("\n")

        if not has_any:
            output.write("\n当前角色没有任何记忆\n")

        # 写入临时文件并发送
        tmp_dir = os.path.join(SCRIPT_DIR, "logs")
        tmp_file = os.path.join(tmp_dir, f"memories_{char_name}_{gid_str}.txt")
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(output.getvalue())
        await send_group_file_fn(websocket, group_id, tmp_file)
        await send_message_fn(websocket, group_id, f"已发送「{char_display}」的记忆文件", at_user=uid_str)
        # 清理临时文件
        os.remove(tmp_file)
        return True

    # ===== 切换角色 =====
    if clean_message.startswith("切换角色"):
        rest = clean_message[4:].strip()
        if not rest:
            await send_message_fn(websocket, group_id, "请指定角色名", at_user=uid_str)
            return True
        if not _is_admin(uid_str):
            await send_message_fn(websocket, group_id, "只有管理员可以切换角色", at_user=uid_str)
            return True
        chars = decision_engine.list_characters()
        char_names = [c[0] for c in chars]
        if rest not in char_names:
            await send_message_fn(websocket, group_id,
                f"找不到角色「{rest}」，可用角色：{', '.join(char_names)}", at_user=uid_str)
            return True
        decision_engine.set_active_character(True, rest)
        await send_message_fn(websocket, group_id, f"已切换到角色「{rest}」", at_user=uid_str)
        return True

    # ===== 角色列表 =====
    if clean_message == "角色列表":
        chars = decision_engine.list_characters()
        active_cfg = decision_engine.get_active_character()
        current = active_cfg.get("character", "")
        if not chars:
            await send_message_fn(websocket, group_id, "还没有创建任何角色", at_user=uid_str)
            return True
        lines = []
        for name, info in chars:
            marker = " ◀ 当前" if name == current else ""
            title = info.get("name", name) if info else name
            lines.append(f"  {title} ({name}){marker}")
        await send_message_fn(websocket, group_id, f"可用角色：\n" + "\n".join(lines), at_user=uid_str)
        return True

    # ===== 启用角色 =====
    if clean_message == "启用角色":
        if not _is_admin(uid_str):
            await send_message_fn(websocket, group_id, "只有管理员可以启用/停用角色", at_user=uid_str)
            return True
        active_cfg = decision_engine.get_active_character()
        if not active_cfg.get("character"):
            chars = decision_engine.list_characters()
            if not chars:
                await send_message_fn(websocket, group_id, "没有可用的角色，请先创建角色", at_user=uid_str)
                return True
            await send_message_fn(websocket, group_id, "请先切换角色", at_user=uid_str)
            return True
        decision_engine.set_active_character(True, active_cfg["character"])
        await send_message_fn(websocket, group_id,
            f"角色模式已启用，当前角色：{active_cfg['character']}", at_user=uid_str)
        return True

    # ===== 停用角色 =====
    if clean_message == "停用角色":
        if not _is_admin(uid_str):
            await send_message_fn(websocket, group_id, "只有管理员可以启用/停用角色", at_user=uid_str)
            return True
        active_cfg = decision_engine.get_active_character()
        decision_engine.set_active_character(False, active_cfg.get("character", ""))
        await send_message_fn(websocket, group_id, "角色模式已停用，回退到默认人设", at_user=uid_str)
        return True

    # ===== 切换模型 =====
    if clean_message.startswith("切换模型") or clean_message.startswith("切模型"):
        rest = re.sub(r"^(切换模型|切模型)\s*", "", clean_message).strip()
        if not rest:
            await send_message_fn(websocket, group_id,
                "请指定模型名，使用「查看模型」查看可用列表", at_user=uid_str)
            return True
        current_model_cfg = decision_engine.get_active_model()
        if rest == current_model_cfg["model"]:
            reply_text = f"当前已在模型「{rest}」，执行连通性测试..."
        else:
            success, result = decision_engine.set_active_model(rest)
            if not success:
                avail = ", ".join(decision_engine.AVAILABLE_MODELS)
                await send_message_fn(websocket, group_id, f"{result}\n可用模型：{avail}", at_user=uid_str)
                return True
            reply_text = f"已切换到模型「{rest}」，执行连通性测试..."
        model_cfg = decision_engine.get_active_model()
        test_result = await test_model(
            f"主模型 ({model_cfg['model']})",
            model_cfg["api_key"], model_cfg["api_base_url"], model_cfg["model"],
        )
        await send_message_fn(websocket, group_id, f"{reply_text}\n\n{test_result}", at_user=uid_str)
        return True

    # ===== 查看模型 =====
    if clean_message in ("查看模型", "模型列表"):
        current_model_cfg = decision_engine.get_active_model()
        current = current_model_cfg["model"]
        lines = ["可用决策引擎主模型（发送 @bot 切换模型 模型名，如：切换模型 glm-5.2）："]
        for model in decision_engine.AVAILABLE_MODELS:
            marker = " ◀ 当前" if model == current else ""
            lines.append(f"  {model}{marker}")
        await send_message_fn(websocket, group_id, "\n".join(lines), at_user=uid_str)
        return True

    # ===== 主帮助 =====
    if clean_message in ("帮助", "help"):
        lines = [f"机器人 v{VERSION} 可用命令（发送 @bot [类别]帮助 查看详情）："]
        for key, short in HELP_SHORT.items():
            lines.append(f"  {key} — {short}")
        reply = "\n".join(lines)
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 子帮助 =====
    for key, cat in HELP_CATEGORIES.items():
        if clean_message == f"{key}帮助":
            lines = [f"【{cat['title']}】"]
            for cmd, desc in cat["commands"]:
                lines.append(f"  @bot {cmd} — {desc}")
            reply = "\n".join(lines)
            await send_message_fn(websocket, group_id, reply, at_user=uid_str)
            return True

    # ===== 今天吃啥 自己 =====
    if clean_message in ("今天吃啥 自己", "我自己吃啥"):
        menu = get_personal_menu(str(group_id), uid_str)
        if not menu:
            reply = "你还没有添加任何菜品，先输入“添加菜单 菜名”吧~"
        else:
            chosen = random.choice(menu)
            reply = f"今天你自己吃：{chosen}"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # ===== 今天吃啥（全群） =====
    if clean_message in ("今天吃啥", "今天吃啥 群", "群里吃啥"):
        items = get_all_dishes(str(group_id))
        if not items:
            reply = "群里还没有任何菜品，大家快用“添加菜单”来加菜吧~"
            await send_message_fn(websocket, group_id, reply, at_user=uid_str)
            return True
        chosen_dish, provider_uid = random.choice(items)
        reply = f"今天全群吃：{chosen_dish}（由 {provider_uid} 提供）"
        await send_message_fn(websocket, group_id, reply, at_user=uid_str)
        return True

    # 没有匹配任何指令
    return False