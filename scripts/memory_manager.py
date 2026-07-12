#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
角色记忆管理 — 独立脚本
========================
交互式管理角色记忆，支持批量添加、删除、查看、切换角色、导入用户画像。
通过 LLM 将自然语言描述自动分类为结构化记忆。

使用方式：
    cd E:\QQbot && python scripts/memory_manager.py
"""

import asyncio
import httpx
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(PROJECT_DIR, "config")
CHARACTERS_DIR = os.path.join(CONFIG_DIR, "characters")
ACTIVE_FILE = os.path.join(CONFIG_DIR, "active_character.json")
PROFILES_FILE = os.path.join(PROJECT_DIR, "user_profiles.json")

# 从 .env 读取 API Key
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

CATEGORY_LABELS = {
    "identity": "身份",
    "relationships": "关系",
    "beliefs": "信念",
    "knowledge": "知识",
    "events": "经历",
    "preferences": "偏好",
}


# ---------- 工具函数 ----------

def load_active_config() -> dict:
    try:
        with open(ACTIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "character": "", "admin_ids": ["784427550"]}


def save_active_config(cfg: dict):
    with open(ACTIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def list_characters() -> list:
    if not os.path.exists(CHARACTERS_DIR):
        return []
    result = []
    for entry in os.listdir(CHARACTERS_DIR):
        entry_path = os.path.join(CHARACTERS_DIR, entry)
        if os.path.isdir(entry_path):
            info_file = os.path.join(entry_path, "info.json")
            info = None
            if os.path.exists(info_file):
                try:
                    with open(info_file, "r", encoding="utf-8") as f:
                        info = json.load(f)
                except Exception:
                    pass
            result.append((entry, info))
    return result


def load_memories(char_name: str) -> dict:
    memories_file = os.path.join(CHARACTERS_DIR, char_name, "memories.json")
    if not os.path.exists(memories_file):
        return {}
    try:
        with open(memories_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_memories(char_name: str, data: dict):
    char_dir = os.path.join(CHARACTERS_DIR, char_name)
    os.makedirs(char_dir, exist_ok=True)
    memories_file = os.path.join(char_dir, "memories.json")
    with open(memories_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- LLM 分类 ----------

async def classify_text(text: str) -> tuple:
    """调用 LLM 将自然语言分类为记忆条目。返回 (category, refined_text)。"""
    system_prompt = (
        "你是一个记忆分类助手。将用户的自然语言描述转换为角色记忆条目。\n"
        "可用类别：identity(身份), relationships(关系), beliefs(信念), "
        "knowledge(知识), events(经历), preferences(偏好)\n"
        "输出 JSON 格式：{\"category\": \"类别名\", \"text\": \"精炼后的记忆语句\"}\n"
        "只输出 JSON，不要其他内容。"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            payload = {
                "model": DEEPSEEK_MODEL,
                "max_tokens": 100,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
            }
            resp = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            # 清理可能的 markdown 包裹
            content = re.sub(r'^```(?:json)?\s*', '', content)
            content = re.sub(r'\s*```$', '', content)
            result = json.loads(content)
            category = result.get("category", "")
            refined = result.get("text", "")
            valid = {"identity", "relationships", "beliefs", "knowledge", "events", "preferences"}
            if category in valid and refined:
                return (category, refined.strip())
            print(f"  ⚠ LLM 返回格式异常: {content[:100]}")
            return ("", "")
    except Exception as e:
        print(f"  ⚠ LLM 调用失败: {e}")
        return ("", "")


async def classify_batch(texts: list) -> list:
    """批量分类，逐条调用 LLM。返回 [(category, text), ...]"""
    results = []
    for i, text in enumerate(texts):
        print(f"  分类 [{i + 1}/{len(texts)}]: {text[:40]}...")
        cat, refined = await classify_text(text)
        if cat and refined:
            results.append((cat, refined))
        else:
            results.append(None)
        await asyncio.sleep(0.5)  # 避免限流
    return results


# ---------- 交互命令 ----------

def show_status():
    cfg = load_active_config()
    current = cfg.get("character", "")
    enabled = "✓" if cfg.get("enabled") else "✗"
    chars = list_characters()
    char_names = [c[0] for c in chars]
    print(f"\n角色模式: {enabled} (启用)")
    if current:
        print(f"当前角色: {current}")
    if chars:
        print(f"可用角色: {', '.join(char_names)}")
    print()


async def cmd_batch_add(char_name: str):
    """批量添加记忆"""
    print(f"\n--- 批量添加记忆到角色「{char_name}」 ---")
    print("逐行输入要添加的记忆，每行一条。空行结束：")
    lines = []
    while True:
        line = input("> ").strip()
        if not line:
            break
        lines.append(line)

    if not lines:
        print("未输入任何内容。")
        return

    print(f"\n正在分类 {len(lines)} 条记忆...")
    results = await classify_batch(lines)

    memories = load_memories(char_name)
    added = 0
    for i, (text, result) in enumerate(zip(lines, results)):
        if result is None:
            print(f"  ✗ 分类失败: {lines[i][:40]}")
            continue
        cat, refined = result
        if cat not in memories:
            memories[cat] = []
        # 检查重复
        if any((item.get("text") if isinstance(item, dict) else item) == refined
               for item in memories[cat]):
            print(f"  ⊘ 已存在: {refined}")
            continue
        memories[cat].append({
            "text": refined,
            "source": "admin",
            "by": "script",
        })
        label = CATEGORY_LABELS.get(cat, cat)
        print(f"  ✓ [{label}] {refined}")
        added += 1

    save_memories(char_name, memories)
    print(f"\n共添加 {added}/{len(lines)} 条记忆。")


async def cmd_batch_add_from_file(char_name: str):
    """从文件批量添加记忆"""
    filepath = input("请输入文件路径（.txt 或 .json 均可）: ").strip()
    if not os.path.exists(filepath):
        print(f"文件不存在: {filepath}")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 支持 txt（每行一条）和 json（字符串数组）
    if filepath.endswith(".json"):
        try:
            lines = json.loads(content)
            if isinstance(lines, dict):
                # 直接是结构化的 JSON，合并到现有记忆
                memories = load_memories(char_name)
                for cat, items in lines.items():
                    if cat not in memories:
                        memories[cat] = []
                    for item in items:
                        if isinstance(item, str):
                            memories[cat].append({"text": item, "source": "admin", "by": "script"})
                        elif isinstance(item, dict):
                            memories[cat].append(item)
                save_memories(char_name, memories)
                print("结构化 JSON 已导入。")
                return
            lines = [str(l) for l in lines]
        except json.JSONDecodeError:
            lines = [l.strip() for l in content.splitlines() if l.strip()]
    else:
        lines = [l.strip() for l in content.splitlines() if l.strip()]

    print(f"文件包含 {len(lines)} 条文本，正在分类...")
    await asyncio.sleep(0.5)
    results = await classify_batch(lines)

    memories = load_memories(char_name)
    added = 0
    for text, result in zip(lines, results):
        if result is None:
            continue
        cat, refined = result
        if cat not in memories:
            memories[cat] = []
        if any((item.get("text") if isinstance(item, dict) else item) == refined
               for item in memories[cat]):
            continue
        memories[cat].append({"text": refined, "source": "admin", "by": "script"})
        added += 1

    save_memories(char_name, memories)
    print(f"共添加 {added}/{len(lines)} 条记忆。")


def cmd_view(char_name: str):
    """查看记忆"""
    memories = load_memories(char_name)
    if not memories:
        print("当前角色没有记忆。")
        return

    for cat, label in CATEGORY_LABELS.items():
        items = memories.get(cat, [])
        if not items:
            continue
        print(f"\n【{label}】")
        for item in items:
            if isinstance(item, dict):
                src = "⭐" if item.get("source") == "admin" else "  "
                by = item.get("by", "")
                print(f"  {src}- {item.get('text', '')} {by}")
            elif isinstance(item, str):
                print(f"    - {item}")


def cmd_delete(char_name: str):
    """删除记忆"""
    cmd_view(char_name)
    keyword = input("\n请输入要删除的记忆的关键词: ").strip()
    if not keyword:
        print("取消删除。")
        return

    memories = load_memories(char_name)
    deleted = 0
    for cat in list(memories.keys()):
        original = len(memories[cat])
        memories[cat] = [
            item for item in memories[cat]
            if keyword not in (item.get("text") if isinstance(item, dict) else item)
        ]
        deleted += original - len(memories[cat])

    save_memories(char_name, memories)
    print(f"已删除 {deleted} 条匹配的记忆。")


def cmd_switch():
    """切换角色"""
    chars = list_characters()
    if not chars:
        print("没有可用的角色。")
        return

    print("\n可用角色：")
    for i, (name, info) in enumerate(chars):
        title = info.get("name", name) if info else name
        print(f"  {i + 1}. {title} ({name})")

    choice = input("请选择角色编号或输入角色名: ").strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(chars):
            choice = chars[idx][0]
        else:
            print("无效的编号。")
            return

    cfg = load_active_config()
    cfg["enabled"] = True
    cfg["character"] = choice
    save_active_config(cfg)
    print(f"已切换到角色「{choice}」并启用。")


def cmd_create():
    """创建新角色"""
    char_name = input("角色标识（英文目录名，如 ayaka）: ").strip()
    if not char_name:
        print("取消创建。")
        return

    char_dir = os.path.join(CHARACTERS_DIR, char_name)
    if os.path.exists(char_dir):
        print(f"角色「{char_name}」已存在。")
        return

    os.makedirs(char_dir, exist_ok=True)

    # 创建 info.json
    name_cn = input("角色中文名: ").strip()
    desc = input("角色简介: ").strip()
    info = {"name": name_cn, "description": desc}
    with open(os.path.join(char_dir, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    # 创建 persona.txt（五段式模板）
    print("\n现在设置角色人设（五段式格式）：")
    print("  输入 /skip 跳过某段，用默认占位内容")
    print()

    sections = {
        "核心身份": "你是一个角色扮演bot。",
        "语言风格": "- 用自然口语回复，一般 1~3 句话\n- 不使用 Markdown 格式\n- 不模仿任何特定角色",
        "行为准则": "- 有礼貌、不骂人\n- 不确定怎么回时，简短地问一句\n- 不要说「作为AI...」等出戏的话",
        "情绪反应": "保持温和友善。",
        "互动策略": "日常闲聊时自然地回应。有人提问时简短回答。有人求助时给实用建议。",
    }
    persona_parts = []
    for title, default_text in sections.items():
        print(f"【## {title}】")
        preview = default_text[:60] + ('...' if len(default_text) > 60 else '')
        print(f"  默认: {preview}")
        lines = []
        print(f"  输入内容（多行，空行结束，或输入 /skip 使用默认值）:")
        while True:
            line = input("  > ").strip()
            if not line:
                break
            if line == "/skip":
                lines = [default_text]
                break
            lines.append(line)
        content = "\n".join(lines) if lines else default_text
        persona_parts.append(f"## {title}\n{content}")
        print()

    persona_text = "\n\n".join(persona_parts)
    with open(os.path.join(char_dir, "persona.txt"), "w", encoding="utf-8") as f:
        f.write(persona_text)

    # 创建空的记忆目录结构
    memories_dir = os.path.join(char_dir, "memories")
    os.makedirs(os.path.join(memories_dir, "character"), exist_ok=True)
    os.makedirs(os.path.join(memories_dir, "group"), exist_ok=True)
    with open(os.path.join(memories_dir, "character", "default.json"), "w", encoding="utf-8") as f:
        json.dump({}, f, ensure_ascii=False, indent=2)

    print(f"\n角色「{char_name}」已创建。")
    print(f"目录：{char_dir}/")
    print(f"  - info.json         角色基本信息")
    print(f"  - persona.txt       角色人设（五段式）")
    print(f"  - memories/character/default.json  通用记忆")
    print(f"  - memories/group/                  群专属记忆")


def cmd_import_profiles(char_name: str):
    """导入用户画像到记忆"""
    if not os.path.exists(PROFILES_FILE):
        print(f"找不到用户画像文件: {PROFILES_FILE}")
        return

    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        profiles_data = json.load(f)

    # 列出所有有画像的群
    if not profiles_data:
        print("用户画像为空。")
        return

    print("\n有画像的群：")
    groups = list(profiles_data.keys())
    for i, gid in enumerate(groups):
        count = len(profiles_data[gid])
        print(f"  {i + 1}. {gid} ({count} 人)")

    choice = input("选择群编号: ").strip()
    if not choice.isdigit():
        return

    gid = groups[int(choice) - 1]
    profiles = profiles_data.get(gid, {})

    # 选择导入方式
    print("\n导入方式：")
    print("  1. 全部导入为 knowledge 类记忆")
    print("  2. 逐条选择（调用 LLM 分类）")
    method = input("选择 (1/2): ").strip()

    # 使用群专属记忆而非角色通用记忆
    group_mem_path = os.path.join(CHARACTERS_DIR, char_name, "memories", "group", f"{gid}.json")
    os.makedirs(os.path.dirname(group_mem_path), exist_ok=True)
    if os.path.exists(group_mem_path):
        try:
            with open(group_mem_path, "r", encoding="utf-8") as f:
                memories = json.load(f)
        except Exception:
            memories = {}
    else:
        memories = {}

    if method == "1":
        if "knowledge" not in memories:
            memories["knowledge"] = []
        imported = 0
        for uid, desc in profiles.items():
            entry = f"群成员 {uid}：{desc}"
            if not any((item.get("text") if isinstance(item, dict) else item) == entry
                       for item in memories["knowledge"]):
                memories["knowledge"].append({
                    "text": entry,
                    "source": "admin",
                    "by": "profiles",
                })
                imported += 1
        with open(group_mem_path, "w", encoding="utf-8") as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        print(f"已导入 {imported} 条画像为群专属 knowledge 记忆。")
    else:
        # 逐条 LLM 分类
        print("正在逐条分类，可能需要较长时间...")

        async def _do_import():
            imported = 0
            for uid, desc in profiles.items():
                text = f"群里有个人 QQ号{uid}，{desc}"
                cat, refined = await classify_text(text)
                if cat and refined:
                    if cat not in memories:
                        memories[cat] = []
                    memories[cat].append({
                        "text": refined,
                        "source": "admin",
                        "by": "profiles",
                    })
                    label = CATEGORY_LABELS.get(cat, cat)
                    print(f"  ✓ [{label}] {refined}")
                    imported += 1
                else:
                    print(f"  ✗ 分类失败: {uid}")
                await asyncio.sleep(0.5)
            with open(group_mem_path, "w", encoding="utf-8") as f:
                json.dump(memories, f, ensure_ascii=False, indent=2)
            print(f"共导入 {imported} 条画像到群专属记忆。")

        asyncio.run(_do_import())


def cmd_persona_manage(char_name: str):
    """查看/编辑角色 Persona"""
    persona_file = os.path.join(CHARACTERS_DIR, char_name, "persona.txt")
    if not os.path.exists(persona_file):
        print(f"角色「{char_name}」没有人设文件。")
        return

    with open(persona_file, "r", encoding="utf-8") as f:
        current = f.read()

    print(f"\n--- 「{char_name}」当前人设 ---")
    print(current)
    print("--- 结束 ---\n")

    print("操作选项：")
    print("  1. 查看（已显示）")
    print("  2. 全部替换")
    print("  3. 编辑某一节")
    print("  0. 返回")

    choice = input("选择: ").strip()
    if choice == "2":
        print("请输入完整的新人设（五段式，Ctrl+Z 结束输入）：")
        lines = []
        try:
            while True:
                line = input()
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        new_text = "\n".join(lines)
        if new_text.strip():
            with open(persona_file, "w", encoding="utf-8") as f:
                f.write(new_text)
            print("人设已全部替换。")
    elif choice == "3":
        sections_list = ["核心身份", "语言风格", "行为准则", "情绪反应", "互动策略"]
        print("可编辑的段落：")
        for i, sec in enumerate(sections_list):
            print(f"  {i + 1}. {sec}")
        sec_choice = input("选择段落编号: ").strip()
        if sec_choice.isdigit():
            idx = int(sec_choice) - 1
            if 0 <= idx < len(sections_list):
                sec_title = sections_list[idx]
                print(f"请输入新的「{sec_title}」内容（多行，空行结束）：")
                lines = []
                while True:
                    line = input("> ").strip()
                    if not line:
                        break
                    lines.append(line)
                if lines:
                    new_section = f"## {sec_title}\n" + "\n".join(lines)
                    import re
                    pattern = rf"## {re.escape(sec_title)}\n.*?(?=\n## |\Z)"
                    new_text = re.sub(pattern, new_section, current, flags=re.DOTALL)
                    if new_text != current:
                        with open(persona_file, "w", encoding="utf-8") as f:
                            f.write(new_text)
                        print(f"「{sec_title}」已更新。")
                    else:
                        print("未找到该段落，请使用「全部替换」。")
                else:
                    print("取消编辑。")
            else:
                print("无效编号。")


def cmd_enable():
    cfg = load_active_config()
    char_name = cfg.get("character", "")
    if not char_name:
        chars = list_characters()
        if not chars:
            print("没有可用的角色。")
            return
        print("请先切换角色。")
        return
    cfg["enabled"] = True
    save_active_config(cfg)
    print(f"角色模式已启用，当前角色：{char_name}")


def cmd_disable():
    cfg = load_active_config()
    cfg["enabled"] = False
    save_active_config(cfg)
    print("角色模式已停用，回退到默认人设。")


# ---------- 主菜单 ----------

def main():
    # 强制 stdout UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    print("========== 角色记忆管理 ==========")

    while True:
        show_status()
        print("请选择操作：")
        print("  1. 批量添加记忆（逐行输入）")
        print("  2. 从文件批量添加记忆")
        print("  3. 查看所有记忆")
        print("  4. 删除记忆")
        print("  5. 切换角色")
        print("  6. 创建新角色")
        print("  7. 导入用户画像到记忆")
        print("  8. 启用角色模式")
        print("  9. 停用角色模式")
        print("  A. 查看/编辑人设")
        print("  0. 退出")

        choice = input("\n选择: ").strip()

        cfg = load_active_config()
        char_name = cfg.get("character", "")

        if choice == "1":
            if not char_name:
                print("请先切换角色。")
                continue
            asyncio.run(cmd_batch_add(char_name))
        elif choice == "2":
            if not char_name:
                print("请先切换角色。")
                continue
            asyncio.run(cmd_batch_add_from_file(char_name))
        elif choice == "3":
            if not char_name:
                print("请先切换角色。")
                continue
            cmd_view(char_name)
        elif choice == "4":
            if not char_name:
                print("请先切换角色。")
                continue
            cmd_delete(char_name)
        elif choice == "5":
            cmd_switch()
        elif choice == "6":
            cmd_create()
        elif choice == "7":
            if not char_name:
                print("请先切换角色。")
                continue
            cmd_import_profiles(char_name)
        elif choice == "8":
            cmd_enable()
        elif choice == "9":
            cmd_disable()
        elif choice.upper() == "A":
            if not char_name:
                print("请先切换角色。")
                continue
            cmd_persona_manage(char_name)
        elif choice == "0":
            print("再见。")
            break
        else:
            print("无效选择。")


if __name__ == "__main__":
    main()