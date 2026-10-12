# -*- coding: utf-8 -*-
"""系统工具 — 模型池连通性测试 + 模型切换/查看。"""

import re
import time

try:
    from scripts.llm_pool import chat, chat_sync, available_models, LLMPoolExhausted
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import chat, chat_sync, available_models, LLMPoolExhausted

try:
    from scripts.llm_pool import (
        load_config as _pool_load_config,
        get_active_model as _pool_get_active_model,
        cooldown_snapshot as _pool_cooldown_snapshot,
        audit_log_path as _pool_audit_log_path,
    )
except ImportError:  # 以脚本方式直接运行时
    from llm_pool import (
        load_config as _pool_load_config,
        get_active_model as _pool_get_active_model,
        cooldown_snapshot as _pool_cooldown_snapshot,
        audit_log_path as _pool_audit_log_path,
    )

from . import _state
from ._registry import register, CommandContext


def pool_base_url() -> str:
    """池的实际出口地址（读不到配置时用本地网关兜底）。"""
    try:
        return str(_pool_load_config().get("base_url") or "").rstrip("/")
    except Exception:
        return "http://127.0.0.1:8090/v1"


def pool_report() -> str:
    """模型池配置摘要（中文输出，=== 分隔）。"""
    lines = ["=== 模型池配置 ===", f"出口: {pool_base_url()}"]
    try:
        cfg = _pool_load_config()
        lines.append(f"API Key: {'已配置' if cfg.get('api_key') else '缺失'}")
        for idx, tier in enumerate(cfg.get("tiers") or [], start=1):
            lines.append(f"T{idx}: " + " → ".join(tier))
        lines.append(
            f"单次尝试 {float(cfg['attempt_timeout_seconds']):.0f}s"
            f" | 总预算 {float(cfg['total_budget_seconds']):.0f}s"
            f" | 冷却 {float(cfg['cooldown_seconds']):.0f}s"
            f"（模型不存在 {float(cfg['hard_cooldown_seconds']):.0f}s）"
        )
    except Exception as e:  # 配置异常也要能看见，而不是整个命令失败
        lines.append(f"配置读取失败: {type(e).__name__}: {e}")

    try:
        active = str(_pool_get_active_model() or "").strip()
    except Exception:
        active = ""
    lines.append(f"当前 active_model: {active or '(未设置)'}")

    try:
        cooling = _pool_cooldown_snapshot()
    except Exception:
        cooling = {}
    lines.append(
        "冷却中: " + (", ".join(f"{m}(剩余{s}s)" for m, s in cooling.items()) if cooling else "无")
    )

    try:
        audit = _pool_audit_log_path()
    except Exception:
        audit = None
    lines.append(f"审计日志: {audit if audit else '(两处均不可写，已静默跳过)'}")
    return "\n".join(lines)


async def test_model(name: str, api_key: str = "", base_url: str = "", model: str = "") -> str:
    """测一次真实调用，返回响应详情（保持 === 分隔的中文输出风格）。

    api_key / base_url 为历史签名保留参数（出口统一为本地模型池后不再使用）；
    model 作为池内首试模型（preferred），留空则用 config/active_model.json。
    """
    t_start = time.time()
    target = model or "(active_model)"
    lines = [f"=== {name} ({target}) ===", f"POST {pool_base_url()}/chat/completions"]

    try:
        text, used = await chat(
            [{"role": "user", "content": "Hi"}],
            label="system_test",
            max_tokens=10,
            timeout=30.0,
            preferred=model or None,
        )
        elapsed = time.time() - t_start
        lines.append(f"Status: 200  |  Time: {elapsed:.2f}s")
        lines.append(f"命中模型: {used}")
        lines.append(f"Reply: {text.strip()[:200]}")
        lines.append("Result: ✅ 连通成功")
    except LLMPoolExhausted as e:
        elapsed = time.time() - t_start
        lines.append(f"Status: EXHAUSTED  |  Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败（池内模型全部失败）")
        lines.append(f"失败明细: {e}")
    except Exception as e:
        elapsed = time.time() - t_start
        lines.append(f"Time: {elapsed:.2f}s")
        lines.append(f"Result: ❌ 连通失败 ({type(e).__name__}: {e})")

    return "\n".join(lines)


@register("test", match_type="exact")
async def cmd_test(ctx: CommandContext) -> bool:
    await ctx.send("正在测试模型池连通性，请稍候...", at_user=ctx.uid_str)
    try:
        active = str(_pool_get_active_model() or "").strip()
    except Exception:
        active = ""
    pool_info = pool_report()
    call_result = await test_model("模型池真实调用", model=active)
    reply = f"模型池测试报告：\n\n{pool_info}\n\n{call_result}"
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
        model_cfg.get("api_key", ""), model_cfg.get("api_base_url", ""), model_cfg["model"],
    )
    await ctx.send(f"{reply_text}\n\n{test_result}", at_user=ctx.uid_str)
    return True


@register("查看模型", match_type="custom",
         pattern=lambda msg: msg in ("查看模型", "模型列表"))
async def cmd_view_model(ctx: CommandContext, _matched=None) -> bool:
    de = _state.decision_engine
    current_model_cfg = de.get_active_model()
    current = current_model_cfg["model"]
    lines = ["可用决策引擎主模型（发送 @bot 切换模型 模型名，如：切换模型 glm-5.3-flash）："]
    for model in de.AVAILABLE_MODELS:
        marker = " ◀ 当前" if model == current else ""
        lines.append(f"  {model}{marker}")
    await ctx.send("\n".join(lines), at_user=ctx.uid_str)
    return True
