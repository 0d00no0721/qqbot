# -*- coding: utf-8 -*-
"""加密 / 解密 — 咕嘎密文（比特级编码）。"""

from ._registry import register, CommandContext


def encrypt(plaintext: str) -> str:
    """将明文按 UTF-8 编码后逐比特展开为「咕」「嘎」序列"""
    if not plaintext:
        return ""
    utf8_bytes = plaintext.encode("utf-8")
    bits = []
    for byte in utf8_bytes:
        for i in range(7, -1, -1):
            bits.append("咕" if (byte >> i) & 1 == 0 else "嘎")
    return "".join(bits)


def decrypt(ciphertext: str) -> str:
    """将咕嘎密文按解密算法恢复为原始明文"""
    if not ciphertext:
        return ""
    bits = []
    for ch in ciphertext:
        if ch == "咕":
            bits.append(0)
        elif ch == "嘎":
            bits.append(1)
        else:
            raise ValueError("密文只能包含「咕」和「嘎」，请检查输入")
    if len(bits) % 8 != 0:
        raise ValueError("密文长度无效，解密失败")
    utf8_bytes = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        utf8_bytes.append(byte)
    try:
        return utf8_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("解密失败，密文可能已损坏")


@register("加密", match_type="prefix")
async def cmd_encrypt(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[2:].strip()
    if not rest:
        await ctx.send("请提供需要加密的文字", at_user=ctx.uid_str)
        return True
    try:
        await ctx.send(encrypt(rest), at_user=ctx.uid_str)
    except Exception as e:
        ctx.log(f"[加密] 加密失败: {e}")
        await ctx.send("加密失败，请检查文字是否包含异常字符", at_user=ctx.uid_str)
    return True


@register("解密", match_type="prefix")
async def cmd_decrypt(ctx: CommandContext) -> bool:
    rest = ctx.clean_message[2:].strip()
    if not rest:
        await ctx.send("请提供需要解密的咕嘎密文", at_user=ctx.uid_str)
        return True
    try:
        plain = decrypt(rest)
        await ctx.send(f"解密结果：{plain}", at_user=ctx.uid_str)
    except ValueError as e:
        await ctx.send(str(e), at_user=ctx.uid_str)
    except Exception as e:
        ctx.log(f"[解密] 解密失败: {e}")
        await ctx.send("解密失败，请检查文字是否包含异常字符", at_user=ctx.uid_str)
    return True
