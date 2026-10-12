# -*- coding: utf-8 -*-
"""本地群消息缓存（V2 识图接线）：解决「先发图 → 再引用提问 → bot 识图」。

为什么需要它（PM 实测取证）：
    手机端的真实形态是「先发一张图，再引用那张图 @bot 提问」——提问那条消息里
    **没有图**，只有一个 `[CQ:reply,id=130731263]`。而 OneBot 的 `get_image` 是
    请求-响应式（要自建 echo 等待表），且历史直链的 rkey **几分钟就过期**
    （实测 19 张历史图 `HTTP 400 download url has expired`）。
    bot 收得到群里的**全量**消息，所以只要在内存里留住最近收到的图元数据，
    按 `message_id` 就能零协议改动地解引用——这就是本模块的全部职责。

三条命中路径的解析顺序（`select_candidates`）：
    1. `本消息`：本条消息自带的图（PC 端批量发图 + @ 提问）；
    2. `引用`：`[CQ:reply,id=…]` 指向的消息 → `by_id()` 命中（⭐手机端主力）；
    3. `最近-同人`：图后直接 @（无引用）→ `recent_by_sender()`（默认 120s 窗口）；
    4. `最近-本群`：再退化为本群最近一张图 → `recent()`。

纪律：
    - **只存元数据**（`image_utils.parse_cq_images` 的产物），绝不存字节/base64；
    - 每群环形缓冲 200 条、TTL 600s（`RING_MAX` / `TTL_SECONDS`）；超时/超量自动不算命中；
    - 全局线程锁；所有查询接口**不抛异常**（脏数据一律当"没命中"）；
    - `image_signature()` 推算的哈希**不带 rkey**，所以不泄露下载凭证，可安全写日志。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Iterable, Optional

# ========== 容量与时间窗（模块级常量，便于调参 / 演练脚本断言） ==========
RING_MAX = 200          # 每群环形缓冲条数上限
TTL_SECONDS = 600       # 缓存条目存活时间（10 分钟）；过期即不算命中
RECENT_DEFAULT = 120    # 路径 ③④ 的默认时间窗（秒）
SEEN_DEFAULT = 60       # 同图去重默认窗口（秒）
PICK_MAX = 3            # 单次识图最多取几张

_CQ_RE = re.compile(r"\[CQ:(?P<type>[a-zA-Z_]+)(?P<params>(?:,[^\],]*)*)\]")
_RKEY_RE = re.compile(r"([?&])(?:rkey|rkey_ts|rkey_t)\b[^&]*")


# ========== CQ 码小工具（只读，不依赖 image_utils，避免反向依赖） ==========
def _cq_matches(raw_message: str) -> Iterable[re.Match]:
    return _CQ_RE.finditer(str(raw_message or ""))


def _cq_params(raw_params: str) -> dict[str, str]:
    """`k=v,k=v` → dict（值做最小反转义，供 id/file 这类字段使用）。"""
    out: dict[str, str] = {}
    for chunk in str(raw_params or "").split(","):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip().replace("&amp;", "&").replace("&#44;", ",")
    return out


def cq_reply_id(raw_message: str) -> str:
    """抽取 `[CQ:reply,id=…]` 里的被引用 message_id；没有则空串。

    注意：必须**先按 CQ 码结构**解析再取 `id`，不能全局 `re.search(r"id=(\\d+)")`
    ——`[CQ:image,...]` 的参数里也可能出现含 `id=` 的片段，会误命中。
    """
    for match in _cq_matches(raw_message):
        if match.group("type").lower() != "reply":
            continue
        value = _cq_params(match.group("params")).get("id", "")
        if value:
            return value
    return ""


def image_signature(meta: dict) -> str:
    """给一张图算一个**稳定且不含 rkey** 的签名字符串（用于同图去重 / 日志）。

    优先级：`file` → URL 路径（AA/BB/hash.image）→ `key` → 原始 url。
    返回空串表示这张图无法签名（调用层按"不去重"处理）。
    """
    meta = meta or {}
    file_name = str(meta.get("file") or "").strip()
    if file_name:
        return file_name
    url = str(meta.get("url") or "").strip()
    if url:
        no_query = _RKEY_RE.sub(r"\1", url)
        no_query = no_query.split("?", 1)[0]
        if len(no_query) > 24:
            tail = no_query.rsplit("/", 2)[-2:]
            if tail and tail[-1]:
                return "/".join(tail)
        if no_query:
            return no_query
    key = str(meta.get("key") or "").strip()
    return key or ""


def image_hash(meta: dict) -> str:
    """`image_signature` 的别名（对外名字，语义：同图去重用的 hash）。"""
    return image_signature(meta)


def describe_entry(entry: dict) -> str:
    """缓存条目的安全摘要（**不含 url / rkey**），可写进日志与报告。"""
    entry = entry or {}
    files = []
    for meta in entry.get("images") or []:
        if not isinstance(meta, dict):
            continue
        name = str(meta.get("file") or "") or image_signature(meta)
        files.append(f"{name}(sub_type={meta.get('sub_type', 0)})")
    return (
        f"mid={entry.get('message_id', '')} uid={entry.get('sender_uid', '')} "
        f"nick={entry.get('nickname', '')} images=[{', '.join(files)}]"
    )


def _norm_within(within: Any, default: float = RECENT_DEFAULT) -> float:
    """把时间窗参数统一成 float；非法值退回 default，None/<=0 视为"用 TTL"。"""
    if within is None:
        return float(TTL_SECONDS)
    try:
        value = float(within)
    except (TypeError, ValueError):
        return float(default)
    if value <= 0:
        return float(TTL_SECONDS)
    return value


def _norm_mid(message_id: Any) -> str:
    """message_id 统一成字符串（NapCat 有时给 int 有时给 str）。"""
    if message_id is None:
        return ""
    if isinstance(message_id, bool):
        return ""
    return str(message_id).strip()


class MessageCache:
    """每群一个环形缓冲的群消息缓存（线程安全）。

    条目结构：`{"mid", "message_id", "sender_uid", "nickname", "images", "ts", "wall"}`；
    `images` 是 `parse_cq_images()` 的产物列表（纯元数据，无字节）。
    `ts` 用 `time.monotonic()`（不受系统改时钟影响），`wall` 用 `time.time()`（人读）。
    """

    def __init__(self, maxlen: int = RING_MAX, ttl: float = TTL_SECONDS):
        self.maxlen = int(maxlen)
        self.ttl = float(ttl)
        self._groups: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.maxlen))
        self._seen: dict[str, deque] = defaultdict(lambda: deque(maxlen=self.maxlen))
        self._lock = threading.RLock()
        self._written = 0
        self._hash_checks = 0
        self._hash_hits = 0

    # ---------- 内部 ----------
    @staticmethod
    def _expired(entry: dict, ttl: float, now: float) -> bool:
        try:
            return (now - float(entry.get("ts", now))) > ttl
        except (TypeError, ValueError):
            return True

    def _prune(self, gid: str, now: float) -> None:
        buf = self._groups.get(gid)
        if buf:
            while buf and self._expired(buf[0], self.ttl, now):
                buf.popleft()

    def _images_of(self, entry: Optional[dict]) -> list[dict]:
        if not entry:
            return []
        return [m for m in (entry.get("images") or []) if isinstance(m, dict)]

    # ---------- 写入 ----------
    def remember(
        self,
        gid: str,
        message_id: Any = None,
        sender_uid: Any = None,
        nickname: Any = "",
        images: Optional[list] = None,
        raw_message: str = "",
    ) -> dict:
        """记下一条群消息（只存元数据）。返回存进去的条目（便于调用层复查）。

        `images` 为空时会尝试从 `raw_message` 现场解析——但反向不会
        （不会把 base64/字节塞进缓存）。message_id 缺失 → 仍入队，只是 `by_id` 命中不到。
        """
        gid = str(gid or "")
        meta_list = [m for m in (images or []) if isinstance(m, dict)]
        if not meta_list and raw_message:
            try:
                from scripts.image_utils import parse_cq_images
            except ImportError:  # pragma: no cover - 独立运行
                from image_utils import parse_cq_images  # type: ignore
            meta_list = parse_cq_images(raw_message)

        entry = {
            "mid": _norm_mid(message_id),
            "message_id": _norm_mid(message_id),
            "sender_uid": str(sender_uid or ""),
            "nickname": str(nickname or ""),
            "images": meta_list,
            "ts": time.monotonic(),
            "wall": time.time(),
        }
        with self._lock:
            self._written += 1
            self._groups[gid].append(entry)
        return entry

    # ---------- 路径 ②：按 message_id 命中 ----------
    def by_id(self, gid: str, message_id: Any) -> Optional[dict]:
        """用被引用的 message_id 取回条目；未命中/已过期 → None。"""
        mid = _norm_mid(message_id)
        if not mid:
            return None
        gid = str(gid or "")
        now = time.monotonic()
        with self._lock:
            self._prune(gid, now)
            for entry in reversed(self._groups.get(gid) or ()):
                if entry.get("mid") == mid:
                    if self._expired(entry, self.ttl, now):
                        return None
                    return entry
        return None

    # ---------- 路径 ③：最近一张"同发送者"的图 ----------
    def recent_by_sender(self, gid: str, sender_uid: Any, within: float = RECENT_DEFAULT) -> list[dict]:
        """同一个人最近发过的图（新→旧），用于"图后直接 @"这种无引用形态。"""
        uid = str(sender_uid or "")
        if not uid:
            return []
        window = _norm_within(within, RECENT_DEFAULT)
        gid = str(gid or "")
        now = time.monotonic()
        with self._lock:
            self._prune(gid, now)
            pool = list(self._groups.get(gid) or ())
        out: list[dict] = []
        for entry in reversed(pool):
            if self._expired(entry, window, now):
                continue
            if str(entry.get("sender_uid") or "") != uid:
                continue
            out.extend(self._images_of(entry))
        return out

    def recent_by_user(self, gid: str, sender_uid: Any, within: float = RECENT_DEFAULT) -> list[dict]:
        """`recent_by_sender` 的别名（两种叫法都接受）。"""
        return self.recent_by_sender(gid, sender_uid, within)

    # ---------- 路径 ④：本群最近一张图 ----------
    def recent(self, gid: str, within: float = RECENT_DEFAULT) -> list[dict]:
        """本群最近发过的图（新→旧），用于"谁发的都行，就一张图"的兜底。"""
        window = _norm_within(within, RECENT_DEFAULT)
        gid = str(gid or "")
        now = time.monotonic()
        with self._lock:
            self._prune(gid, now)
            pool = list(self._groups.get(gid) or ())
        out: list[dict] = []
        for entry in reversed(pool):
            if self._expired(entry, window, now):
                continue
            out.extend(self._images_of(entry))
        return out

    # ---------- 同图去重 ----------
    def seen_hash(self, gid: str, file_hash: str, within: float = SEEN_DEFAULT) -> bool:
        """这张图在窗口内**是否已经识过**。返回 True = 已识过（本次应跳过）。

        - 传空 hash 时返回 False（无法签名 → 不去重）；
        - 命中后**会刷新时间戳**（同图连发不会在窗口边缘被重复识别）。
        """
        sig = str(file_hash or "").strip()
        if not sig:
            return False
        window = _norm_within(within, SEEN_DEFAULT)
        now = time.monotonic()
        gid = str(gid or "")
        with self._lock:
            self._hash_checks += 1
            buf = self._seen[gid]
            while buf and (now - buf[0][1]) > window:
                buf.popleft()
            for sig_seen, _ts in buf:
                if sig_seen == sig:
                    buf.remove((sig_seen, _ts))
                    buf.append((sig, now))
                    self._hash_hits += 1
                    return True
            buf.append((sig, now))
        return False

    # ---------- 观测 ----------
    def stats(self, gid: Optional[str] = None) -> dict:
        """诊断信息（条数/图片数/窗口内图片数/去重计数），**不含 url**。"""
        now = time.monotonic()
        with self._lock:
            if gid is None:
                return {
                    "groups": len(self._groups),
                    "entries": sum(len(b) for b in self._groups.values()),
                    "images": sum(
                        len(self._images_of(e)) for b in self._groups.values() for e in b
                    ),
                    "written": self._written,
                    "hash_checks": self._hash_checks,
                    "hash_hits": self._hash_hits,
                    "ring_max": self.maxlen,
                    "ttl": self.ttl,
                }
            gid = str(gid)
            buf = self._groups.get(gid)
            pool = list(buf or ())
            fresh = [e for e in pool if not self._expired(e, self.ttl, now)]
            recent = [e for e in pool if not self._expired(e, RECENT_DEFAULT, now)]
            return {
                "group": gid,
                "entries": len(pool),
                "fresh": len(fresh),
                "recent": len(recent),
                "images": sum(len(self._images_of(e)) for e in pool),
                "recent_images": sum(len(self._images_of(e)) for e in recent),
                "hash_seen": len(self._seen.get(gid) or ()),
                "ring_max": self.maxlen,
                "ttl": self.ttl,
            }

    def clear(self, gid: Optional[str] = None) -> None:
        """清空（演练脚本/测试用；gid 为 None 时全清）。"""
        with self._lock:
            if gid is None:
                self._groups.clear()
                self._seen.clear()
            else:
                self._groups.pop(str(gid), None)
                self._seen.pop(str(gid), None)


# ========== 默认实例（reverse_bot 直接用模块级函数，避免各处 new 出多个缓存） ==========
default_cache = MessageCache()
_CACHE = default_cache


def remember(gid, message_id=None, sender_uid=None, nickname="", images=None, raw_message=""):
    """默认实例的 `MessageCache.remember`。"""
    return _CACHE.remember(gid, message_id, sender_uid, nickname, images, raw_message)


def by_id(gid, message_id):
    """默认实例的 `MessageCache.by_id`。"""
    return _CACHE.by_id(gid, message_id)


def recent_by_sender(gid, sender_uid, within=RECENT_DEFAULT):
    """默认实例的 `MessageCache.recent_by_sender`。"""
    return _CACHE.recent_by_sender(gid, sender_uid, within)


def recent_by_user(gid, sender_uid, within=RECENT_DEFAULT):
    """默认实例的 `MessageCache.recent_by_user`。"""
    return _CACHE.recent_by_user(gid, sender_uid, within)


def recent(gid, within=RECENT_DEFAULT):
    """默认实例的 `MessageCache.recent`。"""
    return _CACHE.recent(gid, within)


def seen_hash(gid, file_hash, within=SEEN_DEFAULT):
    """默认实例的 `MessageCache.seen_hash`。"""
    return _CACHE.seen_hash(gid, file_hash, within)


def stats(gid=None):
    """默认实例的 `MessageCache.stats`。"""
    return _CACHE.stats(gid)


def clear(gid=None):
    """默认实例的 `MessageCache.clear`。"""
    return _CACHE.clear(gid)


# ========== 三条命中路径的解析入口 ==========
def select_candidates(
    raw_message: str,
    gid: str,
    sender_uid: Any,
    message_images: Optional[list] = None,
    within: float = RECENT_DEFAULT,
    limit: int = PICK_MAX,
    cache: Optional[MessageCache] = None,
) -> tuple[list[dict], str]:
    """按优先级选出这次要识别的图，返回 `(候选列表, 命中路径)`。

    路径取值：`本消息` / `引用` / `最近-同人` / `最近-本群` / `无`。
    候选按"新→旧"排序并去重（同一签名只留第一张），最多 `limit` 张。
    本函数**不做贴图过滤、不做去重、不做限流**——那是调用层（闸门）的职责；
    但会顺手过滤掉没有 url 的残条目。
    """
    cache = cache or _CACHE
    gid = str(gid or "")
    picked: list[dict] = []
    source = "无"

    cur = [m for m in (message_images or []) if isinstance(m, dict)]
    if cur:
        picked, source = cur, "本消息"
    else:
        reply_id = cq_reply_id(raw_message)
        if reply_id:
            entry = cache.by_id(gid, reply_id)
            imgs = [m for m in ((entry or {}).get("images") or []) if isinstance(m, dict)]
            if imgs:
                picked, source = imgs, "引用"
            else:
                # 引用的消息没进缓存 / 不是图 / 已过期 → 继续退化，不报错
                same = cache.recent_by_sender(gid, sender_uid, within)
                if same:
                    picked, source = same, "最近-同人"
                else:
                    group_wide = cache.recent(gid, within)
                    picked, source = (group_wide, "最近-本群") if group_wide else ([], "无")
        else:
            same = cache.recent_by_sender(gid, sender_uid, within)
            if same:
                picked, source = same, "最近-同人"
            else:
                group_wide = cache.recent(gid, within)
                picked, source = (group_wide, "最近-本群") if group_wide else ([], "无")

    out: list[dict] = []
    seen_sigs: set[str] = set()
    for meta in picked:
        if not str((meta or {}).get("url") or "").strip():
            continue
        sig = image_signature(meta)
        if sig and sig in seen_sigs:
            continue
        if sig:
            seen_sigs.add(sig)
        out.append(meta)
        if len(out) >= int(limit):
            break
    return out, source


def dumps_stats(gid: Optional[str] = None) -> str:
    """`stats()` 的 JSON 字符串（日志/报告用）。"""
    try:
        return json.dumps(stats(gid), ensure_ascii=False)
    except Exception:  # pragma: no cover - 纯诊断，绝不抛
        return "{}"
