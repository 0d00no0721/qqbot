"""
上下文驱动发言决策引擎
======================
基于实时群聊上下文（最近N条消息）驱动发言决策。
零样本分类 + 密度动态阈值 + 分类器降级逻辑。

设计原则:
  - 前置过滤器：极简硬规则（过短/纯表情），在昂贵的分类/API 之前拦截
  - 分类器降级：置信度低时不盲目沉默，密度足够高就参与
  - 相似度去重：与近期回复相似度 > 0.75 时跳过，防止刷屏
  - 内容安全过滤：敏感关键词检测，拦截不适宜内容
  - 仅保留 @bot 一条硬规则（强制回复，豁免冷却）
"""

import asyncio
import difflib
import json
import os
import re
import sys
import subprocess
import time
import traceback
import logging
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

from sklearn.metrics.pairwise import cosine_similarity

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- 分类标签 ----------
NEEDS_REPLY_LABELS = ["需要回复", "不需要回复"]

# ---------- 纯 emoji 检测 ----------
_EMOJI_ONLY = re.compile(
    r"^[\U0001F300-\U0001FFFF☀-➿︀-️\U0001F600-\U0001F649"
    r"‍⃣⌚-⌛⏩-⏳▪-◼"
    r"⤴⤵⬅-⬇⬛⬜⭐⭕"
    r"〰〽㊗㊙"
    r"\s]+$"
)

# ---------- 昵称前缀剥离（防止 LLM 模仿上下文格式输出 "[昵称]: 内容"） ----------
_NICKNAME_PREFIX = re.compile(r'^\[.+?\][:：]\s*')

# ---------- 内容安全：敏感关键词列表 ----------
# 安全规则完全由代码层执行，不依赖 LLM prompt。
# 旧版 persona.txt 曾包含安全提示词，要求 LLM 遇到敏感话题时回复
# "这个话题我不太了解"。但该短语同时被 decision_rules.json 的
# error_keywords 拦截 → LLM 输出被代码拒绝 → 机器人保持沉默。
# 这是刻意设计：代码层安全过滤是唯一的安全机制，不信任 LLM 自我审查。
_SENSITIVE_KEYWORDS = [
    # 政治/历史争议
    "皇军", "太君", "鬼子", "汉奸", "伪军", "反动派",
    "文革", "六四", "天安门事件", "法轮功",
    "台独", "藏独", "疆独", "港独",
    "八路",                   # 原 persona.txt 安全规则示例词，与"皇军"同类
    # 种族/民族歧视
    "黑鬼", "白皮猪", "黄祸", "支那",
    # 极端暴力
    "杀人", "自杀", "炸弹制作",
    # 色情/低俗
    "性交", "做爱", "操你", "fuck", "shit",
]

# ---------- 日志 ----------
logger = logging.getLogger("decision_engine")
logger.setLevel(logging.INFO)
logger.propagate = False  # 避免重复输出到根 logger

# 控制台输出
_console = logging.StreamHandler(sys.stderr)
_console.setFormatter(logging.Formatter("[熔断] %(levelname)s: %(message)s"))
logger.addHandler(_console)

# 文件日志（按启动日期命名）
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 文件日志（按启动日期命名，避免 TimedRotatingFileHandler 在 Windows 下的 os.rename 竞态）
from datetime import datetime as _dt
_LOG_DATE = _dt.now().strftime("%Y-%m-%d")
_file_handler = logging.FileHandler(
    os.path.join(LOG_DIR, f"bot.{_LOG_DATE}.log"),
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("[%(asctime)s] [熔断] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger.addHandler(_file_handler)


class ContextRingBuffer:
    """按群存储的环形消息缓冲区。"""

    def __init__(self, max_size: int = 50):
        self._buffer: deque = deque(maxlen=max_size)

    def add(self, user_id: str, nickname: str, content: str, timestamp: float = 0.0):
        if not content or not content.strip():
            return
        self._buffer.append({
            "user_id": str(user_id),
            "nickname": str(nickname),
            "content": str(content).strip(),
            "timestamp": timestamp or time.time(),
        })

    def get_messages(self, max_count: int = 30, max_age_seconds: float = 300) -> list:
        now = time.time()
        messages = []
        for msg in reversed(self._buffer):
            if len(messages) >= max_count:
                break
            if now - msg["timestamp"] > max_age_seconds:
                break
            messages.append(msg)
        messages.reverse()
        return messages

    def get_messages_all(self, max_count: int = 80, max_age_seconds: float = 600) -> list:
        """获取缓冲区中所有消息（用于话题检测，不做截断）。"""
        now = time.time()
        messages = []
        for msg in reversed(self._buffer):
            if len(messages) >= max_count:
                break
            if now - msg["timestamp"] > max_age_seconds:
                break
            messages.append(msg)
        messages.reverse()
        return messages

    def get_density(self, window_seconds: float = 60) -> float:
        """返回消息密度（条/分钟）。"""
        if not self._buffer:
            return 0.0
        now = time.time()
        count = sum(1 for m in self._buffer if now - m["timestamp"] <= window_seconds)
        return count / (window_seconds / 60.0)

    def __len__(self):
        return len(self._buffer)


class DecisionEngine:
    """上下文驱动发言决策引擎。"""

    # 按群冷却时间（秒），防止连续刷屏。@bot 可豁免。
    COOLDOWN_SECONDS = 10
    # 相似度去重阈值
    SIMILARITY_THRESHOLD = 0.75
    # 相似度去重保留的最近回复数
    SIMILARITY_WINDOW = 3

    def __init__(
        self,
        config_path: str = None,
        api_key: str = "",
        api_base_url: str = "",
        api_model: str = "",
        fallback_api_key: str = "",
    ):
        self.config = self._load_config(config_path)
        self._contexts: defaultdict[str, ContextRingBuffer] = defaultdict(
            lambda: ContextRingBuffer(max_size=50)
        )
        self._last_reply: dict = {}
        self._last_reply_time: dict[str, float] = {}       # 冷却计时（按群）
        self._recent_replies: defaultdict[str, deque] = \
            defaultdict(lambda: deque(maxlen=self.SIMILARITY_WINDOW))  # 相似度去重
        self._debug_info: dict = {}
        self._classifier = None

        # 并发安全锁
        self._group_locks: dict[str, asyncio.Lock] = {}  # 按群锁（懒创建）
        self._group_locks_guard = asyncio.Lock()          # 保护 _group_locks 字典
        self._classifier_lock = asyncio.Lock()             # 保护分类器懒加载

        # 内容校验配置（来自 decision_rules.json）
        failover_cfg = self.config.get("failover", {})
        self._content_checks = failover_cfg.get("content_checks", {})

        # 独立脚本路径（与 decision_engine.py 同级目录）
        self._script_dir = os.path.dirname(os.path.abspath(__file__))
        self._primary_script = os.path.join(self._script_dir, "model_primary.py")
        self._fallback_script = os.path.join(self._script_dir, "model_fallback.py")

    # ---------- 配置加载 ----------

    @staticmethod
    def _load_config(config_path: str = None) -> dict:
        default = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "decision_rules.json",
        )
        path = config_path or default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[决策引擎] 加载配置失败: {e}", file=sys.stderr)
            return {
                "context_max_messages": 30,
                "context_max_age_seconds": 300,
                "stable_context_max": 50,
                "stable_context_max_age": 600,
                "topic_similarity_threshold": 0.35,
                "topic_recent_window": 8,
                "topic_scan_window": 5,
                "density_high_threshold": 10,
                "density_low_threshold": 3,
                "max_density_skip": 15,
                "reply_threshold_default": 0.6,
                "classifier_fallback_threshold": 0.45,
                "reply_max_tokens": 500,
                "min_message_length": 3,
            }

    # ---------- 并发控制 ----------

    async def _get_group_lock(self, gid: str) -> asyncio.Lock:
        """获取或创建某群的决策引擎锁（双重检查懒创建）"""
        if gid not in self._group_locks:
            async with self._group_locks_guard:
                if gid not in self._group_locks:
                    self._group_locks[gid] = asyncio.Lock()
        return self._group_locks[gid]

    # ---------- 分类器懒加载 ----------

    async def _get_classifier(self):
        """懒加载分类器（并发安全）"""
        if self._classifier is None:
            async with self._classifier_lock:
                if self._classifier is None:
                    # 按需导入，避免启动时加载 sentence_transformers + torch（数百 MB）
                    from scripts.zero_shot_classifier import ZeroShotClassifier
                    self._classifier = ZeroShotClassifier()
        return self._classifier

    # ---------- 人设加载 ----------

    @staticmethod
    def _load_persona() -> str:
        """从 persona.txt 加载人设（仅性格描述，安全由代码层过滤负责）。"""
        persona_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "persona.txt",
        )
        if os.path.exists(persona_file):
            try:
                with open(persona_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content:
                    return content
            except Exception:
                pass
        return (
            "你是一个QQ群聊机器人。请根据以下对话历史，自然地接话或回复。"
            "回复应符合当前群聊氛围，长度和风格与上下文一致。"
            "不要生成无关内容，不要过度正式。"
        )

    # ---------- 角色加载（persona + 记忆 + 群友画像） ----------

    def _load_character(self, gid: str = "") -> str:
        """加载当前角色 system prompt（persona + memories + 群友画像）。
        根据 gid 查询群→角色映射，回退到全局默认。
        无角色时回退到 _load_persona()。
        """
        # 全局 enabled 开关：停用角色时，所有群都不使用角色
        active_cfg = self.get_active_character()
        if not active_cfg.get("enabled", False):
            return self._load_persona()

        config_dir = self._get_config_dir()

        # 查群→角色映射
        char_name = self.get_character_for_group(gid)
        if not char_name:
            return self._load_persona()

        char_dir = os.path.join(config_dir, "characters", char_name)

        # 加载角色 persona
        persona_text = ""
        persona_file = os.path.join(char_dir, "persona.txt")
        if os.path.exists(persona_file):
            try:
                with open(persona_file, "r", encoding="utf-8") as f:
                    persona_text = f.read().strip()
            except Exception:
                pass
        if not persona_text:
            persona_text = self._load_persona()

        # 指令强化分隔符：告诉 LLM 以上是行为指令（必须遵循），以下是背景知识（仅供参考）
        persona_text += (
            "\n\n---\n"
            "以上是你的角色设定和说话规则。你必须严格遵守其中的语言风格和行为准则。"
            "接下来提供的是你的角色记忆和群聊背景知识——这些是供你参考的信息，"
            "用于了解你「知道什么」，但不应改变你「怎么说话」。"
        )

        # 加载角色通用记忆
        char_memories = self.load_memories(char_name)
        char_memories_formatted = self._format_memories(char_memories)

        # 加载群专属记忆
        group_memories = self.load_group_memories(char_name, gid) if gid else {}
        group_memories_formatted = self._format_memories(group_memories)

        # 组装记忆文本
        memories_text = ""
        if char_memories_formatted or group_memories_formatted:
            mem_parts = []
            if char_memories_formatted:
                mem_parts.append("以下是你的角色记忆（所有群共享）：\n\n" + char_memories_formatted)
            if group_memories_formatted:
                mem_parts.append("以下是你的群专属记忆（当前群）：\n\n" + group_memories_formatted)
            memories_text = "\n\n" + "\n\n---\n\n".join(mem_parts)

        # 加载群友画像
        profile_text = ""
        if gid:
            try:
                profiles_file = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "user_profiles.json",
                )
                if os.path.exists(profiles_file):
                    with open(profiles_file, "r", encoding="utf-8") as f:
                        profiles_data = json.load(f)
                    group_profiles = profiles_data.get(gid, {})
                    if group_profiles:
                        profile_lines = []
                        for uid, desc in group_profiles.items():
                            profile_lines.append(f"- {uid}: {desc}")
                        if profile_lines:
                            profile_text = "\n\n【群友印象】\n" + "\n".join(profile_lines)
            except Exception:
                pass

        # 组装
        system_prompt = persona_text + memories_text + profile_text
        return system_prompt

    # ---------- 模型切换 ----------

    # 可用模型列表（中科大代理）
    AVAILABLE_MODELS = [
        "deepseek-v4-flash-ascend",
        "glm-5.2",
        "deepseek-v4-pro",
        "qwen3.6-chat",
        "qwen3.6-reasoner",
    ]

    @staticmethod
    def _get_model_config_path() -> str:
        """返回 active_model.json 路径。"""
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "active_model.json",
        )

    @staticmethod
    def get_active_model() -> dict:
        """获取当前激活的主模型配置。
        返回: {"model": "...", "api_base_url": "...", "api_key": "..."}
        """
        default = {
            "model": "deepseek-v4-pro",
            "api_base_url": "https://api.llm.ustc.edu.cn/v1/chat/completions",
            "api_key": os.getenv("DECISION_API_KEY", ""),
        }
        path = DecisionEngine._get_model_config_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            model = cfg.get("model", default["model"])
            if model not in DecisionEngine.AVAILABLE_MODELS:
                return default
            return {
                "model": model,
                "api_base_url": default["api_base_url"],
                "api_key": default["api_key"],
            }
        except Exception:
            return default

    @staticmethod
    def set_active_model(model_name: str) -> Tuple[bool, str]:
        """设置主模型。
        返回: (success, message)
        """
        if model_name not in DecisionEngine.AVAILABLE_MODELS:
            return (False, f"模型 '{model_name}' 不可用")
        path = DecisionEngine._get_model_config_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"model": model_name}, f, ensure_ascii=False, indent=2)
            return (True, model_name)
        except Exception as e:
            return (False, f"写入配置失败: {e}")

    # ---------- 记忆管理工具方法 ----------

    @staticmethod
    def _get_char_memories_path(char_name: str) -> str:
        """角色通用记忆路径: config/characters/{char}/memories/character/default.json"""
        return os.path.join(
            DecisionEngine.get_character_dir(char_name),
            "memories", "character", "default.json",
        )

    @staticmethod
    def _get_group_memories_path(char_name: str, gid: str) -> str:
        """群专属记忆路径: config/characters/{char}/memories/group/{gid}.json"""
        return os.path.join(
            DecisionEngine.get_character_dir(char_name),
            "memories", "group", f"{gid}.json",
        )

    @staticmethod
    def _format_memories(data: dict) -> str:
        """将记忆字典格式化为文本。"""
        if not data:
            return ""
        category_names = {
            "identity": "【身份】",
            "relationships": "【关系】",
            "beliefs": "【信念】",
            "knowledge": "【知识】",
            "events": "【经历】",
            "preferences": "【偏好】",
        }
        parts = []
        for cat, label in category_names.items():
            items = data.get(cat, [])
            if not items:
                continue
            sorted_items = sorted(items, key=lambda x: (0 if x.get("source") == "admin" else 1) if isinstance(x, dict) else 1)
            lines = []
            for item in sorted_items:
                if isinstance(item, dict):
                    lines.append("- " + item.get("text", ""))
                elif isinstance(item, str):
                    lines.append("- " + item)
            parts.append(label + "\n" + "\n".join(lines))
        if parts:
            return "\n\n".join(parts)
        return ""

    @staticmethod
    def get_active_character() -> dict:
        """读取 active_character.json，返回配置字典。"""
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
        active_file = os.path.join(config_dir, "active_character.json")
        try:
            with open(active_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"enabled": False, "character": "", "admin_ids": []}

    # ---------- 群→角色映射 ----------

    @staticmethod
    def _get_config_dir() -> str:
        """返回 config/ 目录路径（内部工具方法）。"""
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )

    @staticmethod
    def get_group_characters() -> dict:
        """读取 group_characters.json，返回 {gid: char_name, ...}。
        文件不存在时自动创建（使用全局默认角色 + 当前群）。
        """
        group_file = os.path.join(DecisionEngine._get_config_dir(), "group_characters.json")
        try:
            with open(group_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # 文件不存在或损坏 → 从全局默认初始化
        active_cfg = DecisionEngine.get_active_character()
        default_char = active_cfg.get("character", "")
        if default_char:
            # 用当前主群初始化
            result = {"755471390": default_char}
        else:
            result = {}

        try:
            os.makedirs(os.path.dirname(group_file), exist_ok=True)
            with open(group_file, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[决策引擎] 初始化 group_characters.json 失败: {e}", file=sys.stderr)

        return result

    @staticmethod
    def get_character_for_group(gid: str) -> Optional[str]:
        """获取某群分配的角色名。
        查 group_characters.json[gid] → 有则返回 → 无则回退 active_character.json 全局默认。
        如果全局默认也未启用，返回 None。
        """
        mapping = DecisionEngine.get_group_characters()

        # 优先查群映射
        if gid in mapping:
            char_name = mapping[gid]
            # 验证角色目录存在
            if char_name and os.path.isdir(DecisionEngine.get_character_dir(char_name)):
                return char_name

        # 回退：全局默认
        active_cfg = DecisionEngine.get_active_character()
        if active_cfg.get("enabled") and active_cfg.get("character"):
            return active_cfg["character"]

        return None

    @staticmethod
    def set_character_for_group(gid: str, char_name: str):
        """为某群分配角色（写入 group_characters.json）。"""
        mapping = DecisionEngine.get_group_characters()
        mapping[gid] = char_name
        group_file = os.path.join(DecisionEngine._get_config_dir(), "group_characters.json")
        try:
            with open(group_file, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[决策引擎] 写入 group_characters.json 失败: {e}", file=sys.stderr)

    @staticmethod
    def unset_character_for_group(gid: str):
        """取消某群的群角色分配（回退到全局默认）。"""
        mapping = DecisionEngine.get_group_characters()
        mapping.pop(gid, None)
        group_file = os.path.join(DecisionEngine._get_config_dir(), "group_characters.json")
        try:
            with open(group_file, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[决策引擎] 写入 group_characters.json 失败: {e}", file=sys.stderr)

    @staticmethod
    def get_character_dir(char_name: str) -> str:
        """返回角色目录路径。"""
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
        return os.path.join(config_dir, "characters", char_name)

    @staticmethod
    def get_character_persona(char_name: str) -> str:
        """读取角色 persona.txt，返回文本内容。文件不存在或读取失败时返回空字符串。"""
        persona_file = os.path.join(
            DecisionEngine.get_character_dir(char_name), "persona.txt"
        )
        if os.path.exists(persona_file):
            try:
                with open(persona_file, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                pass
        return ""

    @staticmethod
    def set_character_persona(char_name: str, text: str):
        """写入角色 persona.txt。自动创建角色目录（若不存在）。"""
        char_dir = DecisionEngine.get_character_dir(char_name)
        os.makedirs(char_dir, exist_ok=True)
        persona_file = os.path.join(char_dir, "persona.txt")
        with open(persona_file, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def load_memories(char_name: str) -> dict:
        """加载角色通用记忆（memories/character/default.json）。不存在则返回空字典。"""
        memories_file = DecisionEngine._get_char_memories_path(char_name)
        if not os.path.exists(memories_file):
            return {}
        try:
            with open(memories_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_memories(char_name: str, data: dict):
        """保存角色通用记忆到 memories/character/default.json。"""
        memories_file = DecisionEngine._get_char_memories_path(char_name)
        os.makedirs(os.path.dirname(memories_file), exist_ok=True)
        with open(memories_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def load_group_memories(char_name: str, gid: str) -> dict:
        """加载群专属记忆（memories/group/{gid}.json）。不存在则返回空字典。"""
        memories_file = DecisionEngine._get_group_memories_path(char_name, gid)
        if not os.path.exists(memories_file):
            return {}
        try:
            with open(memories_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def save_group_memories(char_name: str, gid: str, data: dict):
        """保存群专属记忆到 memories/group/{gid}.json。"""
        memories_file = DecisionEngine._get_group_memories_path(char_name, gid)
        os.makedirs(os.path.dirname(memories_file), exist_ok=True)
        with open(memories_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @staticmethod
    def set_active_character(enabled: bool, char_name: str = ""):
        """修改 active_character.json。"""
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
        active_file = os.path.join(config_dir, "active_character.json")
        # 读取现有配置（保留 admin_ids）
        cfg = DecisionEngine.get_active_character()
        cfg["enabled"] = enabled
        cfg["character"] = char_name
        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    @staticmethod
    def get_character_aliases() -> list:
        """获取当前激活角色的别名列表。角色未开启时返回空列表。
        向后兼容：返回全局默认角色的别名。
        """
        active_cfg = DecisionEngine.get_active_character()
        if not active_cfg.get("enabled") or not active_cfg.get("character"):
            return []
        char_name = active_cfg["character"]
        info_file = os.path.join(DecisionEngine.get_character_dir(char_name), "info.json")
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            aliases = info.get("aliases", [])
            # 把角色名本身也加入别名
            name = info.get("name", "")
            if name and name not in aliases:
                aliases.insert(0, name)
            return aliases
        except Exception:
            return []

    @staticmethod
    def get_all_character_aliases() -> dict:
        """获取所有已分配角色的别名映射 {alias_lower: gid}。
        覆盖所有群已分配的角色别名，用于 reverse_bot.py 的别名检测。
        同时加入全局默认角色的别名（用于未配置群的别名匹配）。
        """
        result = {}
        mapping = DecisionEngine.get_group_characters()

        # 遍历每个群的映射
        for gid, char_name in mapping.items():
            if not char_name:
                continue
            aliases = DecisionEngine._get_char_aliases(char_name)
            for alias in aliases:
                alias_lower = alias.lower()
                if alias_lower not in result:
                    result[alias_lower] = gid

        # 加入全局默认角色别名（未配置群使用）
        active_cfg = DecisionEngine.get_active_character()
        if active_cfg.get("enabled") and active_cfg.get("character"):
            default_char = active_cfg["character"]
            # 只加入未在任何群映射中的角色
            if default_char not in mapping.values():
                default_aliases = DecisionEngine._get_char_aliases(default_char)
                for alias in default_aliases:
                    alias_lower = alias.lower()
                    if alias_lower not in result:
                        result[alias_lower] = "default"

        return result

    @staticmethod
    def get_character_info(char_name: str) -> Optional[dict]:
        """读取角色 info.json，返回字典。不存在则返回 None。"""
        info_file = os.path.join(DecisionEngine.get_character_dir(char_name), "info.json")
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _get_char_aliases(char_name: str) -> list:
        """读取角色 info.json，返回别名列表（含角色名本身）。"""
        info_file = os.path.join(DecisionEngine.get_character_dir(char_name), "info.json")
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                info = json.load(f)
            aliases = info.get("aliases", [])
            name = info.get("name", "")
            if name and name not in aliases:
                aliases.insert(0, name)
            return aliases
        except Exception:
            return []

    @staticmethod
    def list_characters() -> list:
        """列出所有可用角色，返回 [(目录名, info.json 内容或 None), ...]"""
        config_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config",
        )
        chars_dir = os.path.join(config_dir, "characters")
        if not os.path.exists(chars_dir):
            return []
        result = []
        for entry in os.listdir(chars_dir):
            entry_path = os.path.join(chars_dir, entry)
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

    # ---------- 内容校验 ----------

    def _validate_content(self, text: str) -> tuple:
        """校验回复内容是否有效。返回 (is_valid: bool, reason: str | None)。"""
        checks = self._content_checks
        min_len = checks.get("min_length", 3)
        max_len = checks.get("max_length", 500)
        error_kws = checks.get("error_keywords", [])
        gibberish_ratio = checks.get("gibberish_max_ratio", 0.6)

        if not text or not text.strip():
            return (False, "空内容")

        text_stripped = text.strip()
        text_len = len(text_stripped)

        if text_len < min_len:
            return (False, f"内容过短 ({text_len} < {min_len})")

        if text_len > max_len:
            return (False, f"内容过长 ({text_len} > {max_len})")

        # 乱码检测
        if self._is_gibberish(text_stripped, gibberish_ratio):
            return (False, "疑似乱码")

        return (True, None)

    @staticmethod
    def _is_gibberish(text: str, max_ratio: float = 0.6) -> bool:
        """检测文本是否大部分为乱码/非自然语言字符。"""
        if not text:
            return False

        valid_chars = 0
        for ch in text:
            if (
                '一' <= ch <= '鿿' or        # CJK 统一表意文字
                '　' <= ch <= '〿' or        # CJK 符号和标点
                '＀' <= ch <= '￯' or        # 全角字符
                'a' <= ch.lower() <= 'z' or
                '0' <= ch <= '9' or
                ch in "，。、；：？！""''（）【】《》～…·.,;:!?()-_[]{}@#$%^&*+=\\/'\"~`<> \t\n"
            ):
                valid_chars += 1

        if len(text) == 0:
            return True

        ratio = valid_chars / len(text)
        return ratio < max_ratio

    # ---------- 独立脚本调用（子进程） ----------

    async def _call_model_script(self, script_path: str, gid: str, max_tokens: int = 500) -> str:
        """通过子进程调用独立模型脚本。

        构造输入 JSON（context + persona + max_tokens + 动态模型配置），
        启动子进程，通过 stdin 发送输入，读取 stdout 中的 JSON 输出。
        """
        logger.info("[调试] _call_model_script 被调用: script=%s, gid=%s", os.path.basename(script_path), gid)
        context_text = await self._format_context(gid)
        system_prompt = self._load_character(gid)

        # 动态读取当前模型配置
        active_model = self.get_active_model()

        input_data = json.dumps({
            "context": context_text,
            "persona": system_prompt,
            "max_tokens": max_tokens,
            "model": active_model["model"],
            "api_base_url": active_model["api_base_url"],
            "api_key": active_model["api_key"],
        }, ensure_ascii=False)

        python_exe = sys.executable or "python"
        try:
            proc = await asyncio.create_subprocess_exec(
                python_exe, script_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=input_data.encode("utf-8")),
                timeout=60.0,
            )
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                logger.error("模型脚本 %s 异常退出 (code=%d): %s",
                             os.path.basename(script_path), proc.returncode, stderr_text[:200])
                return ""

            output = stdout.decode("utf-8", errors="replace").strip()
            stderr_text = stderr.decode("utf-8", errors="replace").strip()

            # 始终记录子进程的 stderr（无论 stdout 是否为空）
            if stderr_text:
                logger.error("模型脚本 %s stderr: %s", os.path.basename(script_path), stderr_text[:512])

            if not output:
                return ""

            result = json.loads(output)
            return (result.get("reply") or "").strip()

        except asyncio.TimeoutError:
            logger.warning("模型脚本 %s 调用超时（>60s）", os.path.basename(script_path))
            return ""
        except json.JSONDecodeError:
            logger.error("模型脚本 %s 输出不是合法 JSON", os.path.basename(script_path))
            return ""
        except Exception as e:
            logger.error("模型脚本 %s 调用失败: %s", os.path.basename(script_path), e)
            return ""

    # ---------- 故障转移回复生成（子进程隔离） ----------

    async def _generate_reply_with_failover(self, gid: str) -> str:
        """生成回复，主模型失败时自动切换备用模型。

        两个模型是独立脚本（子进程），各自维护独立的网络状态，
        主模型的网络问题不会影响备用模型。
        """
        max_tokens = self.config.get("reply_max_tokens", 500)

        # Step 1: 尝试主模型（独立子进程）
        reply = await self._call_model_script(self._primary_script, gid, max_tokens)

        if reply:
            is_valid, reason = self._validate_content(reply)
            if is_valid:
                logger.info("[熔断] 主模型回复有效")
                return reply
            else:
                logger.warning("[熔断] 主模型内容异常 (%s)，切换至备用模型 | 回复内容: %s", reason, reply[:100])
        else:
            logger.warning("[熔断] 主模型无回复，切换至备用模型")

        # Step 2: 备用模型（另一个独立子进程，完全隔离）
        logger.info("[熔断] 调用备用模型（独立进程）")
        reply = await self._call_model_script(self._fallback_script, gid, max_tokens)

        if reply:
            is_valid, reason = self._validate_content(reply)
            if is_valid:
                logger.info("[熔断] 备用模型回复有效")
                return reply
            else:
                logger.warning("[熔断] 备用模型内容异常 (%s)，放弃回复", reason)
        else:
            logger.error("[熔断] 备用模型也无回复")

        return ""

    # ---------- 上下文管理 ----------

    # 话题切换检测参数
    TOPIC_RECENT_WINDOW = 8       # 当前话题窗口（条数）
    TOPIC_SCAN_WINDOW = 5         # 扫描窗口（每次对比的旧消息条数）
    TOPIC_SIM_THRESHOLD = 0.35    # 相似度阈值：低于此值视为话题切换

    def add_message(self, gid: str, uid: str, nickname: str, content: str):
        """写入一条消息到群上下文缓冲区（供外部调用）。"""
        ctx = self._contexts[gid]
        ctx.add(uid, nickname, content)

    async def _detect_topic_switch(self, gid: str) -> int:
        """检测话题切换边界，返回应保留的最近消息条数。

        比较「最近窗口」与前方各扫描窗口的语义相似度。
        找到第一个相似度低于阈值的边界 → 只保留该边界之后的消息。
        若未检测到切换 → 返回 0（使用全部上下文）。

        返回:
            int: 应保留的消息条数（从最新往前数）。0 表示无切换。
        """
        classifier = await self._get_classifier()
        if classifier is None:
            return 0

        cfg = self.config
        scan_n = cfg.get("topic_scan_window", 5)
        recent_n = cfg.get("topic_recent_window", 8)
        similarity_threshold = cfg.get("topic_similarity_threshold", 0.35)

        # 获取所有消息（扩展窗口），排除系统消息
        all_msgs = [
            m for m in self._contexts[gid].get_messages_all(
                max_count=80, max_age_seconds=600
            )
            if m["user_id"] != "system"
        ]
        total = len(all_msgs)

        # 消息太少，无需检测
        if total < recent_n + scan_n:
            return 0

        # 提取最近窗口文本
        recent_texts = [m["content"] for m in all_msgs[-recent_n:]]

        try:
            recent_embs = classifier.encode_batch(recent_texts)
            if len(recent_embs) == 0:
                return 0
            # 最近窗口的质心向量
            recent_centroid = recent_embs.mean(axis=0, keepdims=True)
        except Exception as e:
            print(f"[决策引擎] 话题检测编码失败: {e}", file=sys.stderr)
            traceback.print_exc()
            return 0

        # 从旧到新扫描（跳过最近窗口），寻找话题切换边界
        scan_start = total - recent_n - scan_n  # 扫描起点

        best_sim = 1.0
        best_pos = scan_start + scan_n  # 默认在扫描区末尾

        for pos in range(scan_start, -1, -scan_n):
            window_texts = [m["content"] for m in all_msgs[pos:pos + scan_n]]
            if len(window_texts) < 2:  # 窗口太小，跳过
                continue
            try:
                window_embs = classifier.encode_batch(window_texts)
                if len(window_embs) == 0:
                    continue
                window_centroid = window_embs.mean(axis=0, keepdims=True)
                sim = float(cosine_similarity(recent_centroid, window_centroid)[0][0])
            except Exception:
                continue

            if sim < best_sim:
                best_sim = sim
                best_pos = pos + scan_n

        # 检查最低相似度是否低于阈值
        if best_sim < similarity_threshold:
            # 切换边界 = best_pos（该窗口之后的消息）
            keep_count = total - best_pos
            # 确保至少保留最近窗口
            keep_count = max(keep_count, recent_n)
            return keep_count

        return 0  # 无切换

    async def _format_context(self, gid: str) -> str:
        """将缓冲区中的消息格式化为纯文本上下文（带话题切换检测）。"""
        cfg = self.config

        # 检测话题切换
        switched_count = await self._detect_topic_switch(gid)

        if switched_count > 0:
            # 检测到话题切换，仅保留切换后的消息
            context_max = min(switched_count, cfg.get("context_max_messages", 30))
            max_age = cfg.get("context_max_age_seconds", 300)
            self._debug("话题检测", f"切换边界→保留最近{context_max}条")
        else:
            # 话题稳定，使用扩展上下文
            context_max = cfg.get("stable_context_max", 50)
            max_age = cfg.get("stable_context_max_age", 600)
            self._debug("话题检测", f"未切换→保留{context_max}条")

        ctx = self._contexts[gid]
        msgs = ctx.get_messages_all(
            max_count=context_max,
            max_age_seconds=max_age,
        )

        # 如果检测到切换，进一步裁剪到 switched_count 条以内
        if switched_count > 0 and len(msgs) > switched_count:
            msgs = msgs[-switched_count:]

        if not msgs:
            return "（暂无上下文）"

        lines = []
        for m in msgs:
            # 跳过系统消息（用户画像），不加入上下文显示
            if m["user_id"] == "system":
                continue
            name = m["nickname"] or m["user_id"]
            lines.append(f"[{name}]: {m['content']}")
        return "\n".join(lines)

    def _get_density(self, gid: str) -> float:
        return self._contexts[gid].get_density(60)

    # ---------- 动态阈值 ----------

    def _get_dynamic_threshold(self, density: float) -> float:
        """密度越高阈值越低（更容易回复），密度越低阈值越高（更难触发）。"""
        cfg = self.config
        high = cfg.get("density_high_threshold", 10)
        low = cfg.get("density_low_threshold", 3)
        default = cfg.get("reply_threshold_default", 0.6)

        if density >= high:
            return default - 0.15
        elif density <= low:
            return default + 0.15
        else:
            ratio = (density - low) / max(high - low, 1)
            return (default + 0.15) - ratio * 0.30

    # ---------- 分类 ----------

    async def _classify_needs_reply(self, text: str) -> Tuple[str, float]:
        classifier = await self._get_classifier()
        try:
            label, score = classifier.classify(text, NEEDS_REPLY_LABELS)
            return label, score
        except Exception as e:
            print(f"[决策引擎] 分类器调用失败: {e}", file=sys.stderr)
            return ("不需要回复", 0.0)

    # ---------- 前置过滤器 ----------

    def _pre_filter(self, message: str) -> Optional[str]:
        """返回跳过原因（字符串），或 None 表示通过。"""
        min_len = self.config.get("min_message_length", 3)
        if len(message) < min_len:
            return f"消息过短({len(message)}<{min_len})"
        if _EMOJI_ONLY.match(message):
            return "纯表情"
        return None

    # ---------- 内容安全过滤 ----------

    @staticmethod
    def _content_filter(text: str) -> bool:
        """检测回复内容是否包含敏感关键词。返回 True=通过, False=拦截。"""
        text_lower = text.lower()
        for kw in _SENSITIVE_KEYWORDS:
            if kw.lower() in text_lower:
                print(f"[决策引擎] 内容安全拦截: 匹配关键词 '{kw}'", file=sys.stderr)
                return False
        return True

    # ---------- 回复生成（带故障转移） ----------

    async def _generate_reply_async(self, gid: str) -> str:
        """生成回复。入口统一使用故障转移版本。"""
        return await self._generate_reply_with_failover(gid)

    # ---------- 主决策方法 ----------

    async def should_reply(
        self,
        message: str,
        user_id: str,
        group_id: str,
        is_at_bot: bool = False,
        sender_nickname: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """
        判断是否应回复并生成回复内容。
        返回: (should_reply: bool, reply_text: str | None)
        """
        gid = str(group_id)

        # 按群加锁：同群决策串行，不同群并行
        g_lock = await self._get_group_lock(gid)
        async with g_lock:
            return await self._should_reply_locked(
                message, user_id, group_id, is_at_bot, sender_nickname
            )

    async def _should_reply_locked(
        self,
        message: str,
        user_id: str,
        group_id: str,
        is_at_bot: bool = False,
        sender_nickname: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """should_reply 的内部实现，调用方必须持有 _group_locks[gid]"""
        self._debug_info = {}
        t_start = time.time()
        gid = str(group_id)

        # 确保上下文存在
        if gid not in self._contexts:
            self._contexts[gid] = ContextRingBuffer(max_size=50)

        # ===== @bot 强制回复（唯一硬规则，前置过滤之前判断） =====
        if is_at_bot:
            self._debug("决策", "@bot 强制回复")
            reply = await self._generate_reply_async(gid)
            if not reply:
                self._debug("回复", "API 返回空")
                self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
                return (False, None)
            if not self._content_filter(reply):
                self._debug("安全过滤", "内容包含敏感词，跳过")
                self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
                return (False, None)
            # 精确去重
            last = self._last_reply.get(gid, "")
            if reply == last:
                self._debug("去重", "与上次回复完全相同，跳过")
                self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
                return (False, None)
            self._record_reply(gid, reply)
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (True, reply)

        # ===== 前置过滤器（@bot 已处理，此处仅过滤非 @bot 消息）=====
        skip_reason = self._pre_filter(message)
        if skip_reason:
            self._debug("跳过", skip_reason)
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 冷却检查 =====
        last_time = self._last_reply_time.get(gid, 0)
        elapsed = time.time() - last_time
        if elapsed < self.COOLDOWN_SECONDS:
            self._debug("冷却", f"距上次回复仅{elapsed:.1f}s，跳过")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 密度检查 =====
        density = self._get_density(gid)
        self._debug("密度", f"{density:.2f} 条/分钟")
        max_skip = self.config.get("max_density_skip", 15)
        if density > max_skip:
            self._debug("跳过", f"密度过高 ({density:.1f} > {max_skip})")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 零样本分类 =====
        label, score = await self._classify_needs_reply(message)
        self._debug("分类结果", f"{label} ({score:.4f})")

        # ===== 动态阈值 =====
        threshold = self._get_dynamic_threshold(density)
        self._debug("阈值", f"{threshold:.4f}")

        # ===== 分类器降级逻辑 =====
        fallback_threshold = self.config.get("classifier_fallback_threshold", 0.45)
        if score < fallback_threshold:
            self._debug("降级", f"置信度过低 ({score:.4f} < {fallback_threshold})，密度决策")
            high_density = self.config.get("density_high_threshold", 10)
            if density > high_density:
                self._debug("降级决策", f"密度{density:.1f}>{high_density}，回复")
            else:
                self._debug("降级决策", f"密度{density:.1f}<={high_density}，不回复")
                self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
                return (False, None)

        # ===== 阈值判断 =====
        if label != "需要回复" or score < threshold:
            self._debug("最终", f"不回复 (label={label}, score={score:.4f} < {threshold:.4f})")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        self._debug("最终", f"回复 (label={label}, score={score:.4f})")

        # ===== 生成回复 =====
        reply = await self._generate_reply_async(gid)
        if not reply:
            self._debug("回复", "API 返回空")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 内容安全过滤 =====
        if not self._content_filter(reply):
            self._debug("安全过滤", "内容包含敏感词，跳过")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 精确去重 =====
        last = self._last_reply.get(gid, "")
        if reply == last:
            self._debug("去重", "与上次回复完全相同，跳过")
            self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
            return (False, None)

        # ===== 相似度去重（与最近 N 条回复比较） =====
        recent = self._recent_replies.get(gid, deque(maxlen=self.SIMILARITY_WINDOW))
        for old_reply in recent:
            ratio = difflib.SequenceMatcher(None, reply, old_reply).ratio()
            if ratio > self.SIMILARITY_THRESHOLD:
                self._debug("去重", f"相似度过高 ({ratio:.2f} > {self.SIMILARITY_THRESHOLD})，跳过")
                self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
                return (False, None)

        self._record_reply(gid, reply)
        self._debug("耗时_ms", str(int((time.time() - t_start) * 1000)))
        return (True, reply)

    def _record_reply(self, gid: str, reply: str):
        """记录回复（更新冷却时间、最近回复缓存）。"""
        self._last_reply_time[gid] = time.time()
        self._last_reply[gid] = reply
        self._recent_replies[gid].append(reply)

    # ---------- 调试 ----------

    def _debug(self, key: str, value: str):
        self._debug_info[key] = value

    def get_debug_info(self) -> dict:
        return dict(self._debug_info)  # 返回浅拷贝，避免并发覆盖导致不一致


# ---------- 命令行测试 ----------
if __name__ == "__main__":
    async def test():
        engine = DecisionEngine(
            api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        )
        gid = "test_group"
        engine.add_message(gid, "user_a", "小明", "今天天气真好啊")
        engine.add_message(gid, "user_b", "小红", "是啊适合出去玩")
        engine.add_message(gid, "test_user", "测试用户", "你们在聊什么呢")

        should, reply = await engine.should_reply(
            message="你们在聊什么呢",
            user_id="test_user",
            group_id=gid,
            sender_nickname="测试用户",
        )
        print(f"决策: {'回复' if should else '不回复'}")
        if reply:
            print(f"回复: {reply}")
        print(f"调试: {engine.get_debug_info()}")

    asyncio.run(test())