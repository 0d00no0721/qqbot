# -*- coding: utf-8 -*-
"""帮助系统 — 分层帮助（主帮助 + 子帮助）。"""

from . import _state
from ._registry import register, CommandContext


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
            ("查看人设", "查看当前角色的人设文件"),
            ("设置人设", "（管理员）设置当前角色的说话人设，格式：设置人设 <五段式人设文本>"),
            ("添加记忆 <自然语言>", "向当前群添加角色记忆（需启用角色）"),
            ("删除记忆 <关键词>", "删除包含关键词的群记忆（管理员或不越权删除）"),
            ("查看记忆", "列出当前角色的通用记忆+群记忆"),
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
    "股市": "虚拟股市",
}


@register("帮助", match_type="custom", pattern=lambda msg: msg in ("帮助", "help"))
async def cmd_help(ctx: CommandContext, _matched=None) -> bool:
    lines = [f"机器人 v{_state.VERSION} 可用命令（发送 @bot [类别]帮助 查看详情）："]
    for key, short in HELP_SHORT.items():
        lines.append(f"  {key} — {short}")
    lines.append("  互动菜单 — @bot 互动菜单，或戳机器人后点击表情（需管理员按群启用）")
    reply = "\n".join(lines)
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


def _subhelp_match(msg: str):
    """子帮助匹配：[类别]帮助"""
    for key in HELP_CATEGORIES:
        if msg == f"{key}帮助":
            return key
    return False


@register("子帮助", match_type="custom", pattern=_subhelp_match)
async def cmd_subhelp(ctx: CommandContext, key: str) -> bool:
    cat = HELP_CATEGORIES[key]
    lines = [f"【{cat['title']}】"]
    for cmd, desc in cat["commands"]:
        lines.append(f"  @bot {cmd} — {desc}")
    reply = "\n".join(lines)
    await ctx.send(reply, at_user=ctx.uid_str)
    return True
