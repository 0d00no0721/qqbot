"""识图（多模态）调用池：把一张 data URL 交给池内的视觉模型，拿回一句中文描述。

V1 只交付**能力底座**，不接线到 bot（reverse_bot / 消息缓存 / 功能开关 / 限流
留给下一波 `vision-wire`）。

关键设计（PM 实测定稿）：
- `llm_pool.chat()` 的 messages 是**原样透传**给 `/v1/chat/completions` 的，
  所以 content 用 `[{type:text},{type:image_url,image_url:{url:data:...}}]` 数组
  即可，优先级链 / 冷却 / 审计 / 预算 / 故障转移**自动继承**。
- **必须 `vision=True`**：实测 `qwen3.8-flash-next` 对图片只回 reasoning_content、
  正文为空 → 会被池判为空内容并白烧一轮再降档；`unlimited-ocr` 是 OCR 不是视觉。
  故候选只在 `config/model_pool.json` 的 `vision_models` 白名单内
  （deepseek-flash / glm-5.3-flash / qwen3.8-chat，实测三者均能正确识图）。
- 空正文 / 池耗尽 → `(False, "")`，**不抛异常**，调用点不必 try。

安全纪律：`data_url` 与描述原文都不含 key；本模块不打印 base64。
"""

from __future__ import annotations

from typing import Optional

try:  # 双路兼容：与项目其它模块一致
    from scripts.llm_pool import LLMPoolExhausted, chat
except ImportError:  # pragma: no cover - 独立运行 / 直接跑脚本
    from llm_pool import LLMPoolExhausted, chat  # type: ignore

# 系统提示词：简洁客观中文；敏感内容走安全阀；只输出描述本身
VISION_SYSTEM_PROMPT = (
    "你是一个图片描述助手。请用简洁客观的中文描述用户发来的图片，"
    "1 到 3 句话，总长度不超过 120 字，不要使用 Markdown 或列表。"
    "如果图中有关键文字（招牌、标题、聊天截图里的字、数字等），请摘录其中最重要的部分。"
    "如果图片包含敏感、暴力、色情或其他不适宜内容，只输出「（图片内容不便描述）」，"
    "不要展开任何细节。只输出描述本身，不要写「这张图片」以外的客套话，"
    "不要复述本条要求。"
)
# 兜底提问（调用方没给 hint 时用）
DEFAULT_HINT = "请描述这张图片"

# 识图调用的默认生成参数
VISION_MAX_TOKENS = 300
VISION_TEMPERATURE = 0.3
# 描述长度上限（超出只截断返回，不报错）
DESC_MAX_CHARS = 200
# 敏感内容安全阀输出（原样透传给上层，由接线层决定怎么用）
SENSITIVE_REPLY = "（图片内容不便描述）"


def _extract_text(result) -> str:
    """兼容 chat() 返回 (text, model) 或纯 text 两种形态。"""
    if isinstance(result, tuple):
        return str(result[0] or "").strip() if result else ""
    return str(result or "").strip()


def build_messages(data_url: str, hint: str = "") -> list[dict]:
    """构造多模态 messages（content 数组原样透传给 /chat/completions）。"""
    return [
        {"role": "system", "content": VISION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": hint or DEFAULT_HINT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]


async def describe(data_url: str, hint: str = "") -> tuple[bool, str]:
    """识别一张图，返回 `(ok, 描述文本)`。

    - `data_url`：`scripts.image_utils.to_data_url()` 的产物
      （`data:image/jpeg;base64,...`）；
    - `hint`：可选的提问（如「图里写了什么字」），空则用默认提问；
    - 成功 → `(True, 一句中文描述)`；空正文 / 池耗尽 / 入参不合法 → `(False, "")`；
    - 捕获 `LLMPoolExhausted`（含 RequestError/AuthError 子类）**不向外抛**；
    - 敏感内容：模型按系统提示词只回「（图片内容不便描述）」，本函数原样返回，
      视为**一次成功**（ok=True）——它是有效回答，不是故障。
    """
    url = str(data_url or "").strip()
    if not url.startswith("data:image/"):
        # 只接受 image_utils 产出的 data URL；http 直链/空串一律拒绝
        return False, ""

    messages = build_messages(url, hint)
    try:
        result = await chat(
            messages,
            label="vision",
            vision=True,
            max_tokens=VISION_MAX_TOKENS,
            temperature=VISION_TEMPERATURE,
        )
    except LLMPoolExhausted:
        # 池耗尽 / 请求侧错误 / 鉴权失败：全部降级为"识别失败"，不打扰上游
        return False, ""
    except Exception:
        # 任何意外（网关怪响应等）同样降级，识图失败不该拖垮消息主流程
        return False, ""

    text = _extract_text(result)
    if not text:
        return False, ""
    if len(text) > DESC_MAX_CHARS:
        text = text[:DESC_MAX_CHARS]
    return True, text


async def describe_image_bytes(img_bytes: bytes, hint: str = "") -> tuple[bool, str]:
    """便捷函数：原始字节 → data URL → describe（压缩失败即 (False, "")）。"""
    try:
        from scripts.image_utils import to_data_url_async
    except ImportError:  # pragma: no cover
        from image_utils import to_data_url_async  # type: ignore

    data_url: Optional[str] = await to_data_url_async(img_bytes)
    if not data_url:
        return False, ""
    return await describe(data_url, hint)
