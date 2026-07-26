# -*- coding: utf-8 -*-
"""菜单管理 — 数据层 + 添加/删除/查看/选择/今天吃啥 指令。"""

import json
import os
import random
import re

from . import _state
from ._registry import register, CommandContext


# ========== 数据操作 ==========
def load_data() -> dict:
    if not os.path.exists(_state.DATA_FILE):
        return {}
    try:
        with open(_state.DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        if _state.bot_logger:
            _state.bot_logger.info(f"[菜单] 读取数据失败: {e}")
        return {}


def save_data(data: dict) -> None:
    try:
        with open(_state.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        if _state.bot_logger:
            _state.bot_logger.info(f"[菜单] 保存数据失败: {e}")


def add_dish(group_id: str, user_id: str, dish: str) -> bool:
    data = load_data()
    user_menu = data.setdefault(group_id, {}).setdefault(user_id, [])
    if dish in user_menu:
        return False
    user_menu.append(dish)
    save_data(data)
    return True


def remove_dish(group_id: str, user_id: str, dish: str) -> bool:
    data = load_data()
    group = data.get(group_id, {})
    user_menu = group.get(user_id, [])
    if dish in user_menu:
        user_menu.remove(dish)
        if not user_menu:
            del group[user_id]
        if not group:
            del data[group_id]
        save_data(data)
        return True
    return False


def get_personal_menu(group_id: str, user_id: str) -> list:
    data = load_data()
    return data.get(group_id, {}).get(user_id, [])


def get_all_dishes(group_id: str) -> list:
    """返回 [(dish, provider_uid), ...]"""
    data = load_data()
    group = data.get(group_id, {})
    items = []
    for uid, dishes in group.items():
        for dish in dishes:
            items.append((dish, uid))
    return items


# ========== 指令 ==========
@register("添加菜单", match_type="regex", pattern=r"^(添加菜单|加菜)\s+(.+)$")
async def cmd_add_menu(ctx: CommandContext, match) -> bool:
    dish = match.group(2).strip()
    async with _state._menu_lock:
        if add_dish(str(ctx.group_id), ctx.uid_str, dish):
            reply = f"成功添加「{dish}」到你的菜单~"
        else:
            reply = f"你已经添加过「{dish}」了！"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("删除菜单", match_type="regex", pattern=r"^(删除菜单|删菜)\s+(.+)$")
async def cmd_remove_menu(ctx: CommandContext, match) -> bool:
    dish = match.group(2).strip()
    async with _state._menu_lock:
        if remove_dish(str(ctx.group_id), ctx.uid_str, dish):
            reply = f"已删除「{dish}」~"
        else:
            reply = f"你的菜单中没有「{dish}」，无法删除"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("查看菜单", match_type="exact", pattern="查看菜单")
async def cmd_view_menu(ctx: CommandContext) -> bool:
    menu = get_personal_menu(str(ctx.group_id), ctx.uid_str)
    if not menu:
        reply = "你还没有添加任何菜品，先输入「添加菜单 菜名」吧~"
    else:
        dish_list = "、".join(menu)
        reply = f"你的菜单（共 {len(menu)} 项）：{dish_list}"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("我的菜单", match_type="exact")
async def cmd_my_menu(ctx: CommandContext) -> bool:
    return await cmd_view_menu(ctx)


@register("选择", match_type="regex", pattern=r"^选择\s+(.+)$")
async def cmd_choose(ctx: CommandContext, match) -> bool:
    options = match.group(1).strip().split()
    if len(options) <= 1:
        reply = "你让我选了吗？"
    else:
        chosen = random.choice(options)
        reply = f"我选：{chosen}"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("今天吃啥 自己", match_type="custom",
         pattern=lambda msg: msg in ("今天吃啥 自己", "我自己吃啥"))
async def cmd_eat_self(ctx: CommandContext, _matched=None) -> bool:
    menu = get_personal_menu(str(ctx.group_id), ctx.uid_str)
    if not menu:
        reply = "你还没有添加任何菜品，先输入“添加菜单 菜名”吧~"
    else:
        chosen = random.choice(menu)
        reply = f"今天你自己吃：{chosen}"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


@register("今天吃啥", match_type="custom",
         pattern=lambda msg: msg in ("今天吃啥", "今天吃啥 群", "群里吃咋"))
async def cmd_eat_group(ctx: CommandContext, _matched=None) -> bool:
    items = get_all_dishes(str(ctx.group_id))
    if not items:
        reply = "群里还没有任何菜品，大家快用“添加菜单”来加菜吧~"
        await ctx.send(reply, at_user=ctx.uid_str)
        return True
    chosen_dish, provider_uid = random.choice(items)
    reply = f"今天全群吃：{chosen_dish}（由 {provider_uid} 提供）"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True
