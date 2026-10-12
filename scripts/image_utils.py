"""图片工具集（V1 识图能力底座）：CQ 码解析 / 下载预检 / 压缩成 data URL。

设计要点（PM 实测定稿）：
- 取图：CQ 码里自带 https 直链（`url=https://multimedia.nt.qq.com.cn/download?...&rkey=...`），
  直接 httpx 下载即可 —— 不需要 OneBot `get_image`（要自建请求-响应 echo），
  也不需要读 NapCat 的 `nt_data\\Pic\\` 缓存（实测为空，那条路是死的）。
  CQ 码里带 `file_size`，所以**下载前先预检**，超限直接放弃。
- 压缩：任意尺寸 → Pillow 长边 ≤1024 → JPEG q82 → base64 data URL
  （实测 248KB 原图 → 31.6KB，压到 1/7.9，识别准确）。原始 >8MB 直接拒。
- 优先级链 / 冷却 / 审计继承自 llm_pool，本模块只管"把图变成一段能塞进
  messages 的 data URL"，不做任何模型调用。

安全纪律：本模块任何路径都**绝不打印/记录 base64 内容**（data URL 可能很长，
且属于用户隐私数据）；日志只写长度与元数据。
"""

from __future__ import annotations

import base64
import html
import io
import re
from typing import Any, Optional

import httpx
from PIL import Image

try:  # 双路兼容：作为 scripts.xxx 包导入 / 直接跑脚本时同目录导入
    from scripts.llm_pool import PROJECT_ROOT  # noqa: F401  (保持与项目其它模块一致的导入风格)
except ImportError:  # pragma: no cover - 独立运行
    from llm_pool import PROJECT_ROOT  # type: ignore  # noqa: F401

# CQ 码：`[CQ:image,key=value,key=value]`（值里不含逗号与右括号）
_CQ_RE = re.compile(r"\[CQ:(?P<type>[a-zA-Z_]+)(?P<params>(?:,[^\],]*)*)\]")
# 单张图下载上限：8MB（超过直接拒，不浪费带宽与内存）
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
# 压缩目标：长边 ≤1024，JPEG 质量 82
DEFAULT_MAX_EDGE = 1024
DEFAULT_QUALITY = 82

# 带单位的 file_size（NapCat 偶发 "1.2MB" 这种写法）
_SIZE_UNIT_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([kKmMgG]?)[bB]?\s*$")


def _to_int(value: Any, default: int = 0) -> int:
    """把 CQ 码里的 file_size 之类的值转成 int；转不了给默认值。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return default
    match = _SIZE_UNIT_RE.match(text)
    if match:
        try:
            num = float(match.group(1))
        except ValueError:
            return default
        unit = match.group(2).lower()
        scale = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024 * 1024 * 1024}[unit]
        return int(num * scale)
    try:
        return int(text)
    except ValueError:
        return default


def _parse_params(raw_params: str) -> dict[str, str]:
    """把 CQ 码的参数段解析成 dict（值做 HTML 反转义：&amp; → &）。"""
    params: dict[str, str] = {}
    for chunk in str(raw_params or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if not key:
            continue
        # CQ 码里 url 的 & 会被转义成 &amp;，必须先反转义才能直接用于 httpx
        params[key] = html.unescape(value.strip())
    return params


def parse_cq_images(raw_message: str) -> list[dict]:
    """从一条原始消息里抽取所有 `[CQ:image,...]`。

    返回列表，每项：`{file, url, sub_type, file_size}`（另附带 summary/key 便于接线层
    判贴图与排查；字段缺失时给默认值，绝不抛异常）。

    - `url`：已做 HTML 反转义（`&amp;` → `&`），可直接交给 httpx；
    - `file_size`：转成 int（带 KB/MB 单位也能转），转不了给 0；
    - `sub_type`：转成 int，缺省 0（0=普通图，1=贴图/表情）。
    """
    out: list[dict] = []
    for match in _CQ_RE.finditer(str(raw_message or "")):
        if match.group("type").lower() != "image":
            continue
        params = _parse_params(match.group("params"))
        out.append(
            {
                "file": params.get("file", ""),
                "url": params.get("url", ""),
                "sub_type": _to_int(params.get("sub_type"), 0),
                "file_size": _to_int(params.get("file_size"), 0),
                # 附带字段：接线层判贴图 / 排查用，不属于核心接口
                "summary": params.get("summary", ""),
                "key": params.get("key", ""),
                "raw": match.group(0),
            }
        )
    return out


def parse_cq_image(raw_message: str) -> Optional[dict]:
    """便捷函数：只取第一条图片，没有则 None。"""
    images = parse_cq_images(raw_message)
    return images[0] if images else None


def fetch_image(
    meta: dict,
    timeout: float = 10.0,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Optional[bytes]:
    """下载图片字节；任何异常都返回 None（不抛，调用点不必 try）。

    - **先预检**：`meta['file_size']` 已知且 > max_bytes → 直接 None，不发起请求；
    - 流式下载，边下边限长（`iter_bytes` 累计超限即中止）；
    - `trust_env=False`：本机/内网直连不受系统代理影响（与 llm_pool 同一纪律）；
    - 非 2xx / 空响应体 / 超时 / 网络错误 / rkey 过期 → 一律 None。
    """
    url = str((meta or {}).get("url") or "").strip()
    if not url or not url.lower().startswith(("http://", "https://")):
        return None

    declared = _to_int((meta or {}).get("file_size"), 0)
    if declared > max_bytes:
        return None

    try:
        with httpx.Client(trust_env=False, timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        # 边下边限长：声明值与实际值不一致时也守得住
                        return None
    except Exception:
        # 超时 / 连接失败 / rkey 过期 403 / SSL 错误 …… 全部归为"取不到图"
        return None

    return bytes(buf) if buf else None


def to_data_url(
    img_bytes: bytes,
    max_edge: int = DEFAULT_MAX_EDGE,
    quality: int = DEFAULT_QUALITY,
) -> Optional[str]:
    """把图片字节压缩成 `data:image/jpeg;base64,...`；打不开/压不了返回 None。

    - Pillow 打开（不是图片 → None）；
    - `thumbnail` 等比缩到**长边 ≤ max_edge**（原本就小则不动，不放大）；
    - 统一转 RGB（RGBA/P 带透明通道的先合成到白底，避免透明变黑边）；
    - JPEG quality=quality → base64。返回的字符串可直接塞进
      `{"type":"image_url","image_url":{"url": <返回值>}}`。

    注意：返回值只应传给 llm_pool，**不要写日志/报告**（含用户图片内容）。
    """
    if not img_bytes:
        return None
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img.load()
            edge = int(max_edge) if max_edge and int(max_edge) > 0 else DEFAULT_MAX_EDGE
            if max(img.size) > edge:
                # thumbnail 是原地等比缩放，只缩不放
                img.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            if img.mode != "RGB":
                if img.mode in ("RGBA", "LA", "P"):
                    # 透明通道合成到白底：JPEG 不支持 alpha，直接 convert 会发黑
                    base = Image.new("RGB", img.size, (255, 255, 255))
                    rgba = img.convert("RGBA")
                    base.paste(rgba, mask=rgba.split()[-1])
                    img = base
                else:
                    img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=int(quality or DEFAULT_QUALITY), optimize=True)
    except Exception:
        # 损坏文件 / 不支持的格式 / 解码失败 → 调用点按"没有可用图片"处理
        return None

    payload = out.getvalue()
    if not payload:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii")


def is_sticker(meta: dict) -> bool:
    """是否贴图/动画表情（默认不识别，留给接线层决定要不要跳过）。

    判据：`sub_type == 1`（QQ 的贴图）或 `summary` 含「动画表情」。
    """
    meta = meta or {}
    if _to_int(meta.get("sub_type"), 0) == 1:
        return True
    summary = str(meta.get("summary") or "")
    return "动画表情" in summary


def data_url_bytes(data_url: str) -> int:
    """估算 data URL 解码后的字节数（诊断用，不打印内容）。"""
    text = str(data_url or "")
    if "," not in text:
        return 0
    b64 = text.split(",", 1)[1]
    padding = b64.count("=")
    return max(0, (len(b64) // 4) * 3 - padding)


def describe_meta(meta: dict, url_prefix: int = 80) -> str:
    """元数据摘要（**rkey 打码**，可安全写进报告）。"""
    meta = meta or {}
    url = str(meta.get("url") or "")
    shown = re.sub(r"(rkey=)[^&]*", r"\1<redacted>", url)[:url_prefix]
    return (
        f"file={meta.get('file', '')} file_size={_to_int(meta.get('file_size'), 0)} "
        f"sub_type={_to_int(meta.get('sub_type'), 0)} url={shown}"
    )
