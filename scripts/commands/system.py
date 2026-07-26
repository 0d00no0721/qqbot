# -*- coding: utf-8 -*-
"""系统工具 — 模型连通性测试 + 模型切换/查看。"""

import re
import time

import httpx

from . import _state
from ._registry import register, CommandContext


async def test_model(name: str, api_key: str, base_url: str, model: str) -> str:
    """测试模型 API 连通性，返回原始响应详情"""
    t_start = time.time()
    payload = {
        "model": model, "max_tokens": 10,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    lines = [f"=== {name} ({model}) ===", f"POST {base_url}"]

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(base_url, json=payload, headers=headers)
            elapsed = time.time() - t_start
            lines.append(f"Status: {resp.status_code}  |  Time: {elapsed:.2f}s")

            useful_headers = [
                "content-type", "date", "server", "cf-ray",
                "x-request-id", "x-ratelimit-remaining", "x-ratelimit-limit",
                "x-ds-trace-id", "x-forwarded-for",
            ]
            header_lines = []
            for h in useful_headers:
                val = resp.headers.get(h)
                if val:
                    header_lines.append(f"  {h}: {val}")
            if header_lines:
                lines.append("Response Headers:")
                lines.extend(header_lines)

            raw_body = resp.text
            if len(raw_body) > 800:
                raw_body = raw_body[:800] + "\n... (truncated)"
            lines.append("Response Body:")
            lines.append(raw_body)

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if content:
                        lines.append(f"Reply: {content.strip()}")
                except Exception:
                    pass
                lines.append("Result: ✅ 连通成功")
            else:
                lines.append(f"Result: ❌ 连通失败 (HTTP {resp.status_code})")

    except httpx.TimeoutException as e:
        elapsed = time.time() - t_start
        lines.append(f"Status: TIMEOUT  |  Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败 (TimeoutException: {e})")
    except Exception as e:
        elapsed = time.time() - t_start
        lines.append(f"Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败 ({type(e).__name__}: {e})")

    return "\n".join(lines)


@register("test", match_type="exact")
async def cmd_test(ctx: CommandContext) -> bool:
    await ctx.send("正在测试模型连通性，请稍候...", at_user=ctx.uid_str)
    active_cfg = _state.decision_engine.get_active_model()
    primary_result = await test_model(f"主模型 ({active_cfg['model']})",
        active_cfg["api_key"], active_cfg["api_base_url"], active_cfg["model"])
    fallback_result = await test_model("备用模型 (deepseek-chat)", _state.DEEPSEEK_API_KEY,
        "https://api.deepseek.com/v1/chat/completions", "deepseek-chat")
    reply = f"模型连通性测试报告：\n\n{primary_result}\n\n{fallback_result}"
    await ctx.send(reply, at_user=ctx.uid_str)
    return True


def _switch_model_match(msg: str):
    """切换模型/切模型 前缀匹配（要求空格分隔）"""
    for p in ("切换模型", "切模型"):
        if msg == p or msg.startswith(p + " "):
            return p
    return False


@register("切换模型", match_type="custom", pattern=_switch_model_match)
async def cmd_switch_model(ctx: CommandContext, prefix: str) -> bool:
    rest = re.sub(r"^(切换模型|切模型)\s*", "", ctx.clean_message).strip()
    if not rest:
        await ctx.send("请指定模型名，使用「查看模型」查看可用列表", at_user=ctx.uid_str)
        return True
    de = _state.decision_engine
    current_model_cfg = de.get_active_model()
    if rest == current_model_cfg["model"]:
        reply_text = f"当前已在模型「{rest}」，执行连通性测试..."
    else:
        success, result = de.set_active_model(rest)
        if not success:
            avail = ", ".join(de.AVAILABLE_MODELS)
            await ctx.send(f"{result}\n可用模型：{avail}", at_user=ctx.uid_str)
            return True
        reply_text = f"已切换到模型「{rest}」，执行连通性测试..."
    model_cfg = de.get_active_model()
    test_result = await test_model(
        f"主模型 ({model_cfg['model']})",
        model_cfg["api_key"], model_cfg["api_base_url"], model_cfg["model"],
    )
    await ctx.send(f"{reply_text}\n\n{test_result}", at_user=ctx.uid_str)
    return True


@register("查看模型", match_type="custom",
         pattern=lambda msg: msg in ("查看模型", "模型列表"))
async def cmd_view_model(ctx: CommandContext, _matched=None) -> bool:
    de = _state.decision_engine
    current_model_cfg = de.get_active_model()
    current = current_model_cfg["model"]
    lines = ["可用决策引擎主模型（发送 @bot 切换模型 模型名，如：切换模型 glm-5.2）："]
    for model in de.AVAILABLE_MODELS:
        marker = " ◀ 当前" if model == current else ""
        lines.append(f"  {model}{marker}")
    await ctx.send("\n".join(lines), at_user=ctx.uid_str)
    return True
