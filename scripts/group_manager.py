#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
群功能管理 — 交互式 CLI
=======================
管理每个群的功能开关，支持按群启用/禁用机器人各项能力。

使用方式（PowerShell）：
    cd E:\QQbot
    $env:PYTHONIOENCODING="utf-8"
    python scripts\group_manager.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_DIR = os.path.join(PROJECT_DIR, "config")

FEATURES_FILE = os.path.join(CONFIG_DIR, "group_features.json")
DEFAULTS_FILE = os.path.join(CONFIG_DIR, "group_defaults.json")
GROUP_CHARACTERS_FILE = os.path.join(CONFIG_DIR, "group_characters.json")

# (key, label, description)
FEATURES = (
    ("decision_engine", "决策引擎", "自动判断是否接话并回复"),
    ("plus_one", "+1 复读", "连续两条相同消息时复读"),
    ("commands", "指令响应", "@bot 帮助、Yau、周礼等所有指令"),
    ("newcomer_welcome", "新人欢迎", "新成员入群时自动欢迎"),
    ("profile_record", "用户画像", "记录该群用户发言用于画像更新"),
)
FEATURE_KEYS = tuple(f[0] for f in FEATURES)
FEATURE_LABELS = {f[0]: f[1] for f in FEATURES}
FEATURE_DESCS = {f[0]: f[2] for f in FEATURES}


# ---------- 工具函数 ----------

def _ensure_dirs():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_features() -> dict:
    data = load_json(FEATURES_FILE, None)
    if isinstance(data, dict):
        return data
    return {}


def save_features(data: dict):
    save_json(FEATURES_FILE, data)


def load_defaults() -> dict:
    data = load_json(DEFAULTS_FILE, None)
    if isinstance(data, dict):
        return data
    return {}


def save_defaults(data: dict):
    save_json(DEFAULTS_FILE, data)


def load_group_characters() -> dict:
    data = load_json(GROUP_CHARACTERS_FILE, None)
    if isinstance(data, dict):
        return data
    return {}


def get_default_features() -> dict:
    defaults = load_defaults()
    feat = defaults.get("features", {})
    if isinstance(feat, dict):
        merged = {}
        for key in FEATURE_KEYS:
            merged[key] = feat.get(key, True)
        return merged
    return {key: True for key in FEATURE_KEYS}


def _normalize_entry(entry):
    """确保旧版本数据包含所有功能键。"""
    if not isinstance(entry, dict):
        return {}
    feat = entry.get("features", {})
    defaults = get_default_features()
    merged = {}
    for key in FEATURE_KEYS:
        merged[key] = feat.get(key, defaults[key]) if isinstance(feat, dict) else defaults[key]
    return {
        "group_name": entry.get("group_name", ""),
        "features": merged,
    }


def ensure_group(gid: str):
    """确保群号存在于配置中，不存在则用默认值初始化。"""
    data = load_features()
    if gid in data:
        data[gid] = _normalize_entry(data[gid])
        save_features(data)
        return
    defaults = get_default_features()
    data[gid] = {
        "group_name": "",
        "features": defaults,
    }
    save_features(data)


def discover_groups():
    """发现所有已知群号（合并多个来源）。"""
    gids = set()

    feat_data = load_features()
    gids.update(feat_data.keys())

    char_data = load_group_characters()
    gids.update(char_data.keys())

    # user_profiles.json
    profiles = load_json(os.path.join(PROJECT_DIR, "user_profiles.json"), None)
    if isinstance(profiles, dict):
        gids.update(profiles.keys())

    # known_groups.json
    try:
        kg = load_json(os.path.join(PROJECT_DIR, "known_groups.json"), None)
        if isinstance(kg, list):
            gids.update(str(g) for g in kg)
        elif isinstance(kg, dict):
            gids.update(str(g) for g in kg.keys())
    except Exception:
        pass

    # 确保每个群都在配置中
    for gid in gids:
        ensure_group(str(gid))

    return sorted(str(g) for g in gids)


def get_character_for_group(gid: str) -> str:
    """获取群绑定的角色名。"""
    char_data = load_group_characters()
    val = char_data.get(gid, "")
    if isinstance(val, dict):
        return str(val.get("character", ""))
    return str(val) if val else ""


def _icon(on) -> str:
    return "✓" if on else "✗"


def print_group_list():
    """列出所有群及功能状态。"""
    data = load_features()
    if not data:
        print("\n暂无群数据。请先启动机器人让群被自动发现，或手动添加。")
        return

    print()
    header = f"  {'群号':<15} {'群名':<15} {'角色':<20}"
    header += "  决策  +1   指令  新人  画像"
    print(header)

    for gid in sorted(data.keys()):
        entry = data[gid]
        if not isinstance(entry, dict):
            continue
        name = entry.get("group_name", "") or "(未命名)"
        char = get_character_for_group(gid) or "(默认)"
        feat = entry.get("features", {})
        if not isinstance(feat, dict):
            feat = {}
        icons = ""
        for key in FEATURE_KEYS:
            icons += f"  {_icon(feat.get(key, True))}  "
        print(f"  {gid:<15} {name:<15} {char:<20} {icons}")

    print(f"\n  共 {len(data)} 个群")
    print("  ✓ = 启用  ✗ = 禁用\n")


def print_group_detail(gid, entry):
    """显示单个群的详细信息。"""
    name = entry.get("group_name", "") or "(未命名)"
    char = get_character_for_group(gid) or "(默认)"
    feat = entry.get("features", {})

    print(f"\n  群号: {gid}")
    print(f"  群名: {name}")
    print(f"  角色: {char}")
    print()

    for key, label, desc in FEATURES:
        on = feat.get(key, True)
        print(f"    [{_icon(on)}] {label}: {desc}")

    print()


# ---------- 交互命令 ----------

def cmd_list():
    """列出所有群。"""
    gids = discover_groups()
    if not gids:
        print("\n暂无群数据。")
        return
    print_group_list()


def cmd_edit():
    """编辑某个群的功能。"""
    gids = discover_groups()
    if not gids:
        print("\n暂无群数据。")
        return

    print("\n可选群号：")
    for i, gid in enumerate(gids):
        entry = load_features().get(gid, {})
        name = entry.get("group_name", "") or "(未命名)"
        char = get_character_for_group(gid) or "(默认)"
        print(f"  {i + 1}. {gid} - {name} ({char})")

    choice = input("选择群编号或输入群号 (q 返回): ").strip().lower()
    if choice == "q":
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(gids):
            gid = gids[idx]
        else:
            print("无效的编号。")
            return
    else:
        gid = choice
        ensure_group(gid)

    data = load_features()
    entry = data.get(gid, {})
    if not isinstance(entry, dict):
        entry = {}
    entry = _normalize_entry(entry)
    data[gid] = entry
    save_features(data)

    print_group_detail(gid, entry)

    while True:
        print("  编辑选项:")
        for i, (key, label, desc) in enumerate(FEATURES):
            on = entry["features"].get(key, True)
            print(f"    {i + 1}. {label} (当前: {_icon(on)})")
        n_features = len(FEATURES)
        print(f"    {n_features + 1}. 设置群名")
        print(f"    {n_features + 2}. 设为全部启用")
        print(f"    {n_features + 3}. 设为全部禁用")
        print("    q. 返回")

        choice2 = input("\n  选择: ").strip().lower()

        if choice2 == "q":
            return

        if choice2.isdigit():
            idx = int(choice2)
            if 1 <= idx <= n_features:
                key = FEATURE_KEYS[idx - 1]
                label = FEATURE_LABELS[key]
                current = entry["features"].get(key, True)
                entry["features"][key] = not current
                state = "启用" if entry["features"][key] else "禁用"
                data[gid] = entry
                save_features(data)
                print(f"  -> {label}: {state}")
            elif idx == n_features + 1:
                new_name = input(f"  输入群名 (当前: {entry.get('group_name', '')}): ").strip()
                entry["group_name"] = new_name
                data[gid] = entry
                save_features(data)
                print(f"  -> 群名: {new_name}")
            elif idx == n_features + 2:
                for key in FEATURE_KEYS:
                    entry["features"][key] = True
                data[gid] = entry
                save_features(data)
                print("  -> 全部已启用")
            elif idx == n_features + 3:
                for key in FEATURE_KEYS:
                    entry["features"][key] = False
                data[gid] = entry
                save_features(data)
                print("  -> 全部已禁用")
            else:
                print("无效选择。")
        else:
            print("无效选择。")


def cmd_add_group():
    """手动添加新群号。"""
    gid = input("\n输入群号: ").strip()
    if not gid:
        print("取消。")
        return

    ensure_group(gid)
    data = load_features()
    name = input("输入群名 (可选，直接回车跳过): ").strip()
    if name:
        if gid not in data or not isinstance(data[gid], dict):
            data[gid] = {"group_name": "", "features": get_default_features()}
        data[gid]["group_name"] = name
        save_features(data)

    print(f"已添加群 {gid}，默认功能全部启用。")


def cmd_set_defaults():
    """设置新群默认模板。"""
    defaults = load_defaults()
    feat = defaults.get("features", {})

    print("\n  新群加入时的默认功能配置：")
    for i, (key, label, desc) in enumerate(FEATURES):
        on = feat.get(key, True)
        print(f"    [{_icon(on)}] {i + 1}. {label}: {desc}")

    n_features = len(FEATURES)
    print(f"    {n_features + 1}. 设为全部启用")
    print(f"    {n_features + 2}. 设为全部禁用")
    print("    q. 返回")

    choice = input("\n  选择要切换的功能: ").strip().lower()

    if choice == "q":
        return

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= n_features:
            key = FEATURE_KEYS[idx - 1]
            label = FEATURE_LABELS[key]
            current = feat.get(key, True)
            if not isinstance(feat, dict):
                feat = {}
            feat[key] = not current
            defaults["features"] = feat
            save_defaults(defaults)
            state = "启用" if feat[key] else "禁用"
            print(f"  -> {label}: {state}（新群默认值已更新）")
        elif idx == n_features + 1:
            defaults["features"] = {key: True for key in FEATURE_KEYS}
            save_defaults(defaults)
            print("  -> 新群默认：全部启用")
        elif idx == n_features + 2:
            defaults["features"] = {key: False for key in FEATURE_KEYS}
            save_defaults(defaults)
            print("  -> 新群默认：全部禁用")
        else:
            print("无效选择。")
    else:
        print("无效选择。")


def cmd_batch():
    """批量启用/禁用所有群的某个功能。"""
    data = load_features()
    if not data:
        print("\n暂无群数据。")
        return

    print("\n  选择要批量操作的功能：")
    for i, (key, label, desc) in enumerate(FEATURES):
        print(f"    {i + 1}. {label}")

    choice = input("\n  选择: ").strip()

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(FEATURES):
            key = FEATURE_KEYS[idx - 1]
            label = FEATURE_LABELS[key]
        else:
            print("无效选择。")
            return
    else:
        print("无效选择。")
        return

    action = input(f"  启用还是禁用 {label}？(e=启用/d=禁用): ").strip().lower()
    if action not in ("e", "d"):
        print("取消。")
        return

    val = action == "e"
    count = 0
    for gid, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("features"), dict):
            entry["features"] = get_default_features()
        entry["features"][key] = val
        count += 1

    save_features(data)
    state = "启用" if val else "禁用"
    print(f"  -> 已将 {count} 个群的 {label} 设为 {state}")


def cmd_remove_group():
    """从配置中移除群号。"""
    gids = discover_groups()
    if not gids:
        print("\n暂无群数据。")
        return

    print("\n可选群号：")
    for i, gid in enumerate(gids):
        entry = load_features().get(gid, {})
        name = entry.get("group_name", "") or "(未命名)"
        print(f"  {i + 1}. {gid} - {name}")

    choice = input("\n选择群编号或输入群号 (q 返回): ").strip().lower()
    if choice == "q":
        return

    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(gids):
            gid = gids[idx]
        else:
            print("无效的编号。")
            return
    else:
        gid = choice

    data = load_features()
    if gid not in data:
        print("该群不在配置中。")
        return

    data.pop(gid, None)
    save_features(data)
    print(f"已移除群 {gid}")


# ---------- 主菜单 ----------

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    _ensure_dirs()

    print("========== 群功能管理 ==========")

    while True:
        print("请选择操作：")
        print("  1. 列出所有群")
        print("  2. 编辑群功能")
        print("  3. 手动添加群")
        print("  4. 设置新群默认模板")
        print("  5. 批量启用/禁用功能")
        print("  6. 移除群")
        print("  0. 退出")

        choice = input("\n选择: ").strip()

        if choice == "1":
            cmd_list()
        elif choice == "2":
            cmd_edit()
        elif choice == "3":
            cmd_add_group()
        elif choice == "4":
            cmd_set_defaults()
        elif choice == "5":
            cmd_batch()
        elif choice == "6":
            cmd_remove_group()
        elif choice == "0":
            print("再见。")
            break
        else:
            print("无效选择。")


if __name__ == "__main__":
    main()
