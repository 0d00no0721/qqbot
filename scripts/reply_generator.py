"""
DeepSeek API 调用封装
支持从 .env 文件读取 API Key，也支持环境变量。
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件（位于项目根目录）
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ---------- API 配置 ----------
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

if not DEEPSEEK_API_KEY:
    print("[警告] DEEPSEEK_API_KEY 未设置。请在 .env 文件中填写或设置环境变量。", file=sys.stderr)
elif DEEPSEEK_API_KEY.startswith("sk-") and len(DEEPSEEK_API_KEY) < 20:
    print("[警告] DEEPSEEK_API_KEY 似乎是一个占位符，请填写真实的 API Key。", file=sys.stderr)

# ---------- 人设提示词 ----------
_PERSONA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "persona.txt",
)


def _load_persona() -> str:
    """从外部文件加载人设提示词，失败则使用内置默认"""
    if os.path.exists(_PERSONA_FILE):
        try:
            with open(_PERSONA_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                print(f"[人设] 从 {_PERSONA_FILE} 加载（{len(content)} 字符）")
                return content
        except Exception as e:
            print(f"[人设] 读取失败: {e}", file=sys.stderr)
    print("[人设] 使用内置默认提示词")
    return (
        "你是一个QQ群聊机器人。请根据以下对话历史，自然地接话或回复。"
        "回复应符合当前群聊氛围，长度和风格与上下文一致。"
        "不要生成无关内容，不要过度正式。"
    )


_DEFAULT_SYSTEM_PROMPT = _load_persona()

# ---------- 客户端 ----------
_client = None


def _get_client():
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY 未设置，无法调用 API")
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def generate_reply(
    messages,
    max_tokens=200,
    temperature=0.9,
    top_p=0.95,
    system_prompt=None,
):
    """
    调用 DeepSeek API 生成回复。

    参数:
        messages: list[dict]，格式为 [{"role": "user", "content": "..."}, ...]
                  或 list[str]（会自动转换为 user 消息）。
        max_tokens: 最大输出 token 数
        temperature: 生成温度 (0~2)
        system_prompt: 系统提示词，若为 None 则使用默认人设

    返回:
        reply_text: str，API 返回的文本内容。失败或空回复时返回空字符串。
    """
    client = _get_client()

    system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    # 如果 messages 是纯字符串列表，转为 OpenAI 格式
    formatted_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        if isinstance(msg, dict):
            formatted_messages.append(msg)
        elif isinstance(msg, str):
            formatted_messages.append({"role": "user", "content": msg})
        else:
            formatted_messages.append({"role": "user", "content": str(msg)})

    try:
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=formatted_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        content = response.choices[0].message.content
        return (content or "").strip()
    except Exception as e:
        print(f"[generate_reply] API 调用失败: {e}", file=sys.stderr)
        return ""


# ---------- 快捷函数 ----------
def reply_to_text(text, max_tokens=200, temperature=0.7):
    """直接对用户文本生成回复（单轮，无上文）"""
    return generate_reply([{"role": "user", "content": text}], max_tokens, temperature)


if __name__ == "__main__":
    # 测试
    if not DEEPSEEK_API_KEY:
        print("请先设置 DEEPSEEK_API_KEY 再测试")
        sys.exit(1)
    result = reply_to_text("今天天气真好啊")
    print(f"回复: {result}")
