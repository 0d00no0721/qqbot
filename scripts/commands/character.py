# -*- coding: utf-8 -*-
"""角色与记忆管理 — 添加/删除/查看记忆、查看/设置人设、切换/列表/启用/停用角色。"""

import io
import json
import os
import re

from . import _state
from ._helpers import call_deepseek
from ._registry import register, CommandContext


async def _classify_memory(text: str, char_name: str):
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
        ctx_log(f"[记忆分类] LLM 返回格式异常: {content[:100]}")
        return ("", "")
    except Exception as e:
        ctx_log(f"[记忆分类] 调用失败: {e}")
        return ("", "")


def ctx_log(msg: str) -> None:
    if _state.bot_logger:
        _state.bot_logger.info(msg)


def _need_character(ctx: CommandContext):
    """检查角色模式与群角色配置，返回 (char_name, error_reply)。成功时 error_reply 为 None。"""
    de = _state.decision_engine
    if not de:
        return (None, "决策引擎未初始化")
    active_cfg = de.get_active_character()
    if not active_cfg.get("enabled"):
        return (None, "角色模式未启用，请先启用角色")
    gid_str = str(ctx.group_id)
    char_name = de.get_character_for_group(gid_str)
    if not char_name:
        return (None, "当前群未配置角色，请先启用角色")
    return (char_name, None)


@register("添加记忆", match_type="prefix")
async def cmd_add_memory(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[4:].strip()
    if not rest:
        await ctx.send("请提供要添加的记忆内容", at_user=ctx.uid_str)
        return True
    char_name, err = _need_character(ctx)
    if err:
        await ctx.send(err, at_user=ctx.uid_str)
        return True
    gid_str = str(ctx.group_id)
    source = "admin" if ctx.is_admin() else "user"
    category, refined_text = await _classify_memory(rest, char_name)
    if not category or not refined_text:
        await ctx.send("记忆分类失败，请重试", at_user=ctx.uid_str)
        return True
    memories = _state.decision_engine.load_group_memories(char_name, gid_str)
    if category not in memories:
        memories[category] = []
    for existing in memories[category]:
        if isinstance(existing, dict) and existing.get("text") == refined_text:
            await ctx.send(f"这条记忆已经存在了", at_user=ctx.uid_str)
            return True
    memories[category].append({"text": refined_text, "source": source, "by": ctx.uid_str})
    _state.decision_engine.save_group_memories(char_name, gid_str, memories)
    cat_label = {"identity": "身份", "relationships": "关系", "beliefs": "信念",
                 "knowledge": "知识", "events": "经历", "preferences": "偏好"}.get(category, category)
    await ctx.send(f"已添加群记忆 [{cat_label}]：{refined_text}", at_user=ctx.uid_str)
    return True


@register("删除记忆", match_type="prefix")
async def cmd_delete_memory(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[4:].strip()
    if not rest:
        await ctx.send("请提供删除记忆的关键词", at_user=ctx.uid_str)
        return True
    char_name, err = _need_character(ctx)
    if err:
        await ctx.send(err, at_user=ctx.uid_str)
        return True
    gid_str = str(ctx.group_id)
    memories = _state.decision_engine.load_group_memories(char_name, gid_str)
    deleted = 0
    is_admin = ctx.is_admin()
    for cat in list(memories.keys()):
        original_count = len(memories[cat])
        new_items = []
        for item in memories[cat]:
            item_text = item.get("text") if isinstance(item, dict) else item
            # 关键词不匹配 → 保留
            if rest not in item_text:
                new_items.append(item)
                continue
            # 关键词匹配 → 检查删除权限
            can_delete = is_admin or (
                isinstance(item, dict) and item.get("by") == ctx.uid_str
            )
            if not can_delete:
                new_items.append(item)  # 无权限，保留
            # 有权限 → 不加入 new_items（即删除）
        memories[cat] = new_items
        deleted += original_count - len(memories[cat])
    _state.decision_engine.save_group_memories(char_name, gid_str, memories)
    await ctx.send(f"已删除 {deleted} 条匹配的群记忆", at_user=ctx.uid_str)
    return True


@register("查看记忆", match_type="exact")
async def cmd_view_memory(ctx: CommandContext) -> bool:
    char_name, err = _need_character(ctx)
    if err:
        await ctx.send(err, at_user=ctx.uid_str)
        return True
    gid_str = str(ctx.group_id)
    de = _state.decision_engine
    char_memories = de.load_memories(char_name)
    group_memories = de.load_group_memories(char_name, gid_str)
    cat_labels = {
        "identity": "身份", "relationships": "关系", "beliefs": "信念",
        "knowledge": "知识", "events": "经历", "preferences": "偏好",
    }
    char_info = de.get_character_info(char_name)
    char_display = char_info.get("name", char_name) if char_info else char_name

    output = io.StringIO()
    output.write(f"角色: {char_display}\n")
    output.write(f"群: {gid_str}\n")
    output.write("=" * 40 + "\n\n")

    has_any = False
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

    tmp_dir = os.path.join(_state.SCRIPT_DIR, "logs")
    tmp_file = os.path.join(tmp_dir, f"memories_{char_name}_{gid_str}.txt")
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(output.getvalue())
    await ctx.send_file(tmp_file)
    await ctx.send(f"已发送「{char_display}」的记忆文件", at_user=ctx.uid_str)
    os.remove(tmp_file)
    return True


@register("查看人设", match_type="exact")
async def cmd_view_persona(ctx: CommandContext) -> bool:
    char_name, err = _need_character(ctx)
    if err:
        await ctx.send(err, at_user=ctx.uid_str)
        return True
    de = _state.decision_engine
    persona_text = de.get_character_persona(char_name)
    if not persona_text:
        await ctx.send(f"角色「{char_name}」没有人设文件", at_user=ctx.uid_str)
        return True
    char_info = de.get_character_info(char_name)
    char_display = char_info.get("name", char_name) if char_info else char_name

    tmp_dir = os.path.join(_state.SCRIPT_DIR, "logs")
    tmp_file = os.path.join(tmp_dir, f"persona_{char_name}_{str(ctx.group_id)}.txt")
    line_count = persona_text.count('\n') + 1
    char_count = len(persona_text)
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(persona_text)
    await ctx.send_file(tmp_file)
    await ctx.send(f"已发送「{char_display}」的人设文件（{line_count} 行，{char_count} 字）", at_user=ctx.uid_str)
    os.remove(tmp_file)
    return True


@register("设置人设", match_type="prefix")
async def cmd_set_persona(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[4:].strip()
    if not rest:
        await ctx.send(
            "请提供人设文本。格式：\n设置人设\n## 核心身份\n...\n\n## 语言风格\n- ...\n\n## 行为准则\n- ...\n\n## 情绪反应\n...\n\n## 互动策略\n...",
            at_user=ctx.uid_str)
        return True
    if not ctx.is_admin():
        await ctx.send("只有管理员可以设置人设", at_user=ctx.uid_str)
        return True
    gid_str = str(ctx.group_id)
    char_name = _state.decision_engine.get_character_for_group(gid_str)
    if not char_name:
        await ctx.send("当前群未配置角色，无法设置人设", at_user=ctx.uid_str)
        return True

    # 验证五段式结构（至少包含核心三段标题）
    required_sections = ["## 核心身份", "## 语言风格", "## 行为准则"]
    missing = [s for s in required_sections if s not in rest]
    if missing:
        await ctx.send(f"人设缺少必需段落：{', '.join(missing)}。请使用五段式格式。", at_user=ctx.uid_str)
        return True

    _state.decision_engine.set_character_persona(char_name, rest)
    char_count = len(rest)
    char_info = _state.decision_engine.get_character_info(char_name)
    char_display = char_info.get("name", char_name) if char_info else char_name
    await ctx.send(f"已更新「{char_display}」的人设（{char_count} 字）", at_user=ctx.uid_str)
    return True


@register("切换角色", match_type="prefix")
async def cmd_switch_character(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[4:].strip()
    if not rest:
        await ctx.send("请指定角色名", at_user=ctx.uid_str)
        return True
    if not ctx.is_admin():
        await ctx.send("只有管理员可以切换角色", at_user=ctx.uid_str)
        return True
    chars = _state.decision_engine.list_characters()
    char_names = [c[0] for c in chars]
    if rest not in char_names:
        await ctx.send(f"找不到角色「{rest}」，可用角色：{', '.join(char_names)}", at_user=ctx.uid_str)
        return True
    _state.decision_engine.set_active_character(True, rest)
    await ctx.send(f"已切换到角色「{rest}」", at_user=ctx.uid_str)
    return True


@register("角色列表", match_type="exact")
async def cmd_character_list(ctx: CommandContext) -> bool:
    chars = _state.decision_engine.list_characters()
    active_cfg = _state.decision_engine.get_active_character()
    current = active_cfg.get("character", "")
    if not chars:
        await ctx.send("还没有创建任何角色", at_user=ctx.uid_str)
        return True
    lines = []
    for name, info in chars:
        marker = " ◀ 当前" if name == current else ""
        title = info.get("name", name) if info else name
        lines.append(f"  {title} ({name}){marker}")
    await ctx.send(f"可用角色：\n" + "\n".join(lines), at_user=ctx.uid_str)
    return True


@register("启用角色", match_type="exact")
async def cmd_enable_character(ctx: CommandContext) -> bool:
    if not ctx.is_admin():
        await ctx.send("只有管理员可以启用/停用角色", at_user=ctx.uid_str)
        return True
    de = _state.decision_engine
    active_cfg = de.get_active_character()
    if not active_cfg.get("character"):
        chars = de.list_characters()
        if not chars:
            await ctx.send("没有可用的角色，请先创建角色", at_user=ctx.uid_str)
            return True
        await ctx.send("请先切换角色", at_user=ctx.uid_str)
        return True
    de.set_active_character(True, active_cfg["character"])
    await ctx.send(f"角色模式已启用，当前角色：{active_cfg['character']}", at_user=ctx.uid_str)
    return True


@register("停用角色", match_type="exact")
async def cmd_disable_character(ctx: CommandContext) -> bool:
    if not ctx.is_admin():
        await ctx.send("只有管理员可以启用/停用角色", at_user=ctx.uid_str)
        return True
    de = _state.decision_engine
    active_cfg = de.get_active_character()
    de.set_active_character(False, active_cfg.get("character", ""))
    await ctx.send("角色模式已停用，回退到默认人设", at_user=ctx.uid_str)
    return True
