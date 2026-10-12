# -*- coding: utf-8 -*-
"""周礼 — 将大白话改写成"合乎周礼"白话翻译腔（走中央模型池，自动故障转移）。"""

import re
import time

try:
    from scripts.llm_pool import chat, chat_sync, available_models, LLMPoolExhausted
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import chat, chat_sync, available_models, LLMPoolExhausted

from . import _state
from ._registry import register, CommandContext


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

# 出口统一为中央模型池（本地网关），不再持有任何云端端点与 key
ZHOULI_MAX_TOKENS = 300
ZHOULI_TIMEOUT = 30


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
    usage = _state.zhouli_usage[user_id]
    _state.zhouli_usage[user_id] = [t for t in usage if now - t < _state.ZHOULI_RATE_WINDOW]
    if len(_state.zhouli_usage[user_id]) >= _state.ZHOULI_RATE_LIMIT:
        return False
    _state.zhouli_usage[user_id].append(now)
    return True


async def call_zhouli_api(text: str, preferred: str = "") -> str:
    """调用中央模型池生成周礼文案。

    preferred 指定首试模型（留空则用 config/active_model.json 的当前模型）；
    池内部按优先级链自动降档，全部失败抛 LLMPoolExhausted。
    """
    messages = [
        {"role": "system", "content": ZHOULI_SYSTEM_PROMPT},
        {"role": "user", "content": f"请将下面这句话改写得合乎周礼：\n<原话>\n{text}\n</原话>"},
    ]
    raw, model = await chat(
        messages,
        label="zhouli",
        max_tokens=ZHOULI_MAX_TOKENS,
        temperature=0.9,
        timeout=float(ZHOULI_TIMEOUT),
        preferred=preferred or None,
    )
    ctx_log(f"[周礼] 模型池命中 ({model})")
    return raw


async def call_zhouli(text: str) -> str:
    """调用周礼 API（池内按优先级链故障转移），全部失败返回空串"""
    try:
        raw = await call_zhouli_api(text)
        if raw and raw.strip():
            ctx_log("[周礼] 生成成功")
            return clean_zhouli_text(raw)
        ctx_log("[周礼] 模型返回空内容")
    except LLMPoolExhausted as e:
        ctx_log(f"[周礼] 模型池耗尽: {e}")
    except Exception as e:
        ctx_log(f"[周礼] 调用异常: {e}")

    return ""


def ctx_log(msg: str) -> None:
    if _state.bot_logger:
        _state.bot_logger.info(msg)


@register("周礼", match_type="prefix")
async def cmd_zhouli(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[2:].strip()
    if not rest:
        await ctx.send("请提供需要改写的话，格式：周礼 <文字>", at_user=ctx.uid_str)
        return True
    if len(rest) > _state.ZHOULI_MAX_LENGTH:
        await ctx.send(f"文字太长，请控制在{_state.ZHOULI_MAX_LENGTH}字以内", at_user=ctx.uid_str)
        return True
    async with _state._zhouli_lock:
        if not check_zhouli_rate_limit(ctx.uid_str):
            await ctx.send("您已达到每小时使用次数上限（3次），请稍后再试。", at_user=ctx.uid_str)
            return True
    ctx.log(f"[周礼] 开始处理: {rest[:30]}...")
    try:
        await ctx.send("礼官正在斟酌，请稍候~", at_user=ctx.uid_str)
        reply = await call_zhouli(rest)
        if reply:
            ctx.log(f"[周礼] 完成，{len(reply)}字")
        else:
            reply = "礼官远行未归，请稍后再试。"
            ctx.log("[周礼] 双模型均失败")
        await ctx.send(reply, at_user=ctx.uid_str)
    except Exception as e:
        ctx.log(f"[周礼] 处理异常: {e}")
        import traceback
        traceback.print_exc()
        await ctx.send("礼官远行未归，请稍后再试。", at_user=ctx.uid_str)
    return True
