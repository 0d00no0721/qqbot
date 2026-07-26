# -*- coding: utf-8 -*-
"""SimPiano 钢琴 — 曲谱解析 + numpy 波形合成 + WAV 发送。"""

import asyncio
import html
import os
import re
import time

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from . import _state
from ._registry import register, CommandContext


class SimPianoError(Exception):
    """自定义异常，包含错误位置和描述"""
    def __init__(self, pos, msg):
        self.pos = pos
        self.msg = msg
        super().__init__(f"位置 {pos}: {msg}")


def _note_to_midi(base, sharps, flats, octave_shift):
    """将音高参数转换为 MIDI 音符号"""
    semitone = [0, 2, 4, 5, 7, 9, 11]
    note = semitone[base - 1]
    note += sharps
    note -= flats
    return 60 + octave_shift * 12 + note


def _midi_to_freq(midi):
    return 440.0 * (2 ** ((midi - 69) / 12.0))


_NOTE_RE = re.compile(r'^(\**)([1-7])(\**)([#!]?)(~*)$')
_NOTE_RE_GROUP = re.compile(r'^(\**)([1-7])(\**)([#!]?)(~*)')
_TEMPO_RE = re.compile(r'^\[(\d+)\]$', re.ASCII)


def _parse_note(token, pos):
    """解析单个音符 token，返回 (midi, dur_beats)"""
    m = _NOTE_RE.match(token)
    if not m:
        raise SimPianoError(pos, f"无效的音符格式: '{token}'")
    pre_star, digit, post_star, accidental, tildes = m.groups()
    base = int(digit)
    octave = len(post_star) - len(pre_star)
    sharps = 1 if accidental == '#' else 0
    flats = 1 if accidental == '!' else 0
    midi = _note_to_midi(base, sharps, flats, octave)
    return midi, 1 + len(tildes)


def _parse_rest(token, pos):
    """解析休止符 token，返回持续时间（拍）"""
    if token[0] != '-':
        raise SimPianoError(pos, "内部错误：非休止符传入 parse_rest")
    if set(token) == {'-'}:
        return len(token)
    if not re.fullmatch(r'-~+', token):
        raise SimPianoError(pos, f"无效的休止符格式: '{token}'")
    return 1 + token.count('~')


def _parse_group(token, pos):
    """解析括号分组 token。"""
    if not (token.startswith('(') and token.endswith(')')):
        raise SimPianoError(pos, f"分组括号不匹配: '{token}'")
    inner = token[1:-1]
    if not inner:
        raise SimPianoError(pos, "空括号")

    extend_shares = 0
    while inner.startswith('~'):
        extend_shares += 1
        inner = inner[1:]

    elements = []
    i = 0
    while i < len(inner):
        if inner[i].isspace():
            i += 1
            continue
        sub = inner[i:]
        m = _NOTE_RE_GROUP.match(sub)
        if m:
            elements.append(('note', m.group(0)))
            i += len(m.group(0))
            continue
        m_rest = re.match(r'-~*', sub)
        if m_rest:
            elements.append(('rest', m_rest.group(0)))
            i += len(m_rest.group(0))
            continue
        raise SimPianoError(pos, f"括号内无效格式，位置 {i}")

    shares = [(1 + tok.count('~')) for _, tok in elements]

    if extend_shares > 0:
        raw_list = []
        for (typ, tok), share in zip(elements, shares):
            if typ == 'note':
                midi, _ = _parse_note(tok, pos)
                raw_list.append(('note', midi, share))
            else:
                raw_list.append(('rest', share))
        return ('extend', extend_shares, raw_list)

    total = sum(shares)
    sub_events = []
    for (typ, tok), share in zip(elements, shares):
        dur = share / total
        if typ == 'note':
            midi, _ = _parse_note(tok, pos)
            sub_events.append(('note', midi, dur))
        else:
            sub_events.append(('rest', dur))
    return sub_events, 1.0


def custom_split(text):
    """智能分词：识别括号组 (...) 和速度标记 [...] 为独立 token。"""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '(':
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                if text[j] == '(':
                    depth += 1
                elif text[j] == ')':
                    depth -= 1
                j += 1
            if depth != 0:
                raise SimPianoError(i, "括号未闭合")
            tokens.append(text[i:j])
            i = j
        elif ch == '[':
            j = i + 1
            while j < n and text[j] != ']':
                j += 1
            if j >= n:
                raise SimPianoError(i, "方括号未闭合")
            j += 1
            tokens.append(text[i:j])
            i = j
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()[]':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def tokenize_and_parse(text):
    """主解析函数：返回事件列表。"""
    text = html.unescape(text)
    tokens = custom_split(text)
    tokens = [t for t in tokens if t != '|']
    if not tokens:
        return []

    raw_events = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        token = token.strip()
        token = re.sub(r'[​‌‍⁠﻿]', '', token)

        m = _TEMPO_RE.match(token)
        if not m:
            m2 = re.match(r'^\[(\d+)\]$', token.strip())
            if m2:
                m = m2
        if m:
            bpm = int(m.group(1))
            if bpm <= 0:
                raise SimPianoError(i, f"速度必须是正整数，得到 {bpm}")
            raw_events.append(('tempo', bpm))
            i += 1
            continue

        if token.startswith('('):
            if not token.endswith(')'):
                raise SimPianoError(i, "括号未闭合")
            result = _parse_group(token, i)
            if isinstance(result, tuple) and len(result) == 3 and result[0] == 'extend':
                raw_events.append(('__extend__', result[1], result[2]))
            else:
                sub_events, _ = result
                raw_events.extend(sub_events)
            i += 1
            continue
        if token.endswith(')') and not token.startswith('('):
            raise SimPianoError(i, "意外的右括号")

        if token[0] == '-':
            raw_events.append(('rest', _parse_rest(token, i)))
            i += 1
            continue

        if token[0] in '*1234567':
            if not _NOTE_RE.match(token):
                raise SimPianoError(i, f"无效的音符格式: '{token}'")
            midi, dur = _parse_note(token, i)
            raw_events.append(('note', midi, dur))
            i += 1
            continue

        raise SimPianoError(i, f"无法识别的符号: '{token}'")

    events = []
    for ev in raw_events:
        if ev[0] == '__extend__':
            _, extra, sub_events = ev
            if not events:
                raise SimPianoError(0, "延长括号前没有音符")
            last_idx = len(events) - 1
            while last_idx >= 0 and events[last_idx][0] == 'tempo':
                last_idx -= 1
            if last_idx < 0:
                raise SimPianoError(0, "延长括号前没有音符")

            last_ev = events.pop(last_idx)

            if last_ev[0] == 'note':
                _, last_midi, last_dur = last_ev
                last_share = last_dur
            elif last_ev[0] == 'rest':
                _, last_dur = last_ev
                last_share = last_dur
            else:
                raise SimPianoError(0, f"延长括号前不支持的事件类型: {last_ev[0]}")

            sub_shares = []
            for sub_ev in sub_events:
                if sub_ev[0] == 'note':
                    sub_shares.append(sub_ev[2])
                else:
                    sub_shares.append(sub_ev[1])

            extra_beats = extra
            prev_weight = extra
            total_weight = prev_weight + sum(sub_shares)

            prev_extra = extra_beats * prev_weight / total_weight
            prev_total_dur = last_share + prev_extra

            new_events = []
            if last_ev[0] == 'note':
                new_events.append(('note', last_midi, prev_total_dur))
            else:
                new_events.append(('rest', prev_total_dur))

            for sub_ev, share in zip(sub_events, sub_shares):
                sub_dur = extra_beats * share / total_weight
                if sub_ev[0] == 'note':
                    new_events.append(('note', sub_ev[1], sub_dur))
                else:
                    new_events.append(('rest', sub_dur))

            events[last_idx:last_idx] = new_events
        else:
            events.append(ev)

    if not any(ev[0] == 'tempo' for ev in events):
        events.insert(0, ('tempo', 120))
    return events


_SAMPLE_RATE = 44100


def _generate_piano_wave(midi, duration_sec, sample_rate=_SAMPLE_RATE):
    """生成钢琴音色波形（基频 + 泛音 + 指数衰减包络）"""
    freq = _midi_to_freq(midi)
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    harmonics = [1.0, 0.5, 0.25, 0.125, 0.06]
    wave = np.zeros_like(t)
    for k, weight in enumerate(harmonics, 1):
        wave += weight * np.sin(2 * np.pi * k * freq * t)
    env = np.ones_like(t)
    attack_samples = int(0.01 * sample_rate)
    if attack_samples > 0 and len(env) > attack_samples:
        env[:attack_samples] = np.linspace(0, 1, attack_samples)
    decay = duration_sec * 0.4
    if decay > 0:
        env = env * np.exp(-t / decay)
    wave *= env
    max_abs = np.max(np.abs(wave))
    if max_abs > 0:
        wave = wave / max_abs
    return wave


def events_to_audio(events, sample_rate=_SAMPLE_RATE):
    """将事件列表转换为音频波形数组，返回 (audio, sample_rate)"""
    abs_events = []
    current_time = 0.0
    current_bpm = 120
    for ev in events:
        if ev[0] == 'tempo':
            current_bpm = ev[1]
        else:
            beat_sec = 60.0 / current_bpm
            if ev[0] == 'note':
                dur_sec = ev[2] * beat_sec
                abs_events.append(('note', current_time, ev[1], dur_sec))
                current_time += dur_sec
            elif ev[0] == 'rest':
                current_time += ev[1] * beat_sec
    if not abs_events:
        return np.zeros(int(0.5 * sample_rate)), sample_rate
    total = max(s + d for _, s, _, d in abs_events) + 0.1
    audio = np.zeros(int(total * sample_rate))
    for _, start, midi, dur in abs_events:
        w = _generate_piano_wave(midi, dur, sample_rate)
        si = int(start * sample_rate)
        ei = si + len(w)
        if ei > len(audio):
            audio = np.pad(audio, (0, ei - len(audio)))
        audio[si:ei] += w
    max_v = np.max(np.abs(audio))
    if max_v > 1.0:
        audio = audio / max_v * 0.9
    return audio, sample_rate


def write_wav(filename, audio, sample_rate):
    """将 numpy 数组写入 16-bit PCM WAV 文件"""
    import wave as _wave
    audio_int16 = np.int16(audio * 32767)
    with _wave.open(filename, 'w') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_int16.tobytes())


def _piano_synthesize(events, out_path):
    """合成音频并写入 WAV 文件（供线程池调用）"""
    audio, sr = events_to_audio(events)
    write_wav(out_path, audio, sr)


def _piano_match(msg: str):
    """钢琴指令匹配：保留裸 startswith（曲谱无空格，如「钢琴[80]1 2 3」）"""
    if msg.startswith("钢琴"):
        return True
    return False


@register("钢琴", match_type="custom", pattern=_piano_match)
async def cmd_piano(ctx: CommandContext, _matched=None) -> bool:
    rest = ctx.clean_message[2:].strip()
    if rest in ("帮助", "help"):
        help_path = os.path.join(_state.SCRIPT_DIR, "SimPiano.md")
        try:
            with open(help_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            ctx.log(f"[钢琴] 读取编码标准文件失败: {e}")
            content = "无法读取编码标准文件，请联系管理员"
        await ctx.send(content, at_user=ctx.uid_str)
        return True

    if not HAS_NUMPY:
        await ctx.send("钢琴功能缺少依赖，请联系管理员安装 numpy（pip install numpy）", at_user=ctx.uid_str)
        return True
    score = rest
    if not score:
        await ctx.send("请提供 SimPiano 曲谱，示例：[80] 1 1 5 5 | 6 6 5~", at_user=ctx.uid_str)
        return True
    out_path = None
    try:
        events = tokenize_and_parse(score)
        ctx.log(f"[钢琴] 解析成功，{len(events)} 个事件，来自 {ctx.user_id}")
        out_path = os.path.join(_state.SCRIPT_DIR, f"piano_{int(time.time())}.wav")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: _piano_synthesize(events, out_path))
        await ctx.send_file(out_path)
        ctx.log(f"[钢琴] 已发送 {os.path.basename(out_path)}")
    except SimPianoError as e:
        await ctx.send(f"曲谱解析失败：{e.msg}", at_user=ctx.uid_str)
        return True
    except Exception as e:
        ctx.log(f"[钢琴] 合成失败: {e}")
        import traceback
        traceback.print_exc()
        await ctx.send("钢琴合成失败，请检查曲谱格式或联系管理员", at_user=ctx.uid_str)
        return True
    finally:
        if out_path and os.path.exists(out_path):
            os.remove(out_path)
    return True
