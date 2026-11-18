"""JSON / Markdown 导出：逐行 yield，交给 StreamingResponse 流式返回。

消息量虽小（N<=20），但流式生成让导出内存占用恒定，也避免拼接巨型字符串。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime

def _local(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000).astimezone()


def _iso(ms: int) -> str:
    return _local(ms).isoformat(timespec="seconds")


def _md_escape(s: str) -> str:
    """只转义昵称（结构文本），消息正文保持原文。"""
    for ch in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        s = s.replace(ch, "\\" + ch)
    return s


def _display_name(m: dict) -> tuple[str, str]:
    avatar = m.get("avatar") or "🙂"
    nick = m.get("nickname") or "匿名"
    if m.get("sender_type") == "ai":
        # 规格第八节：DSH 仅靠头像 + 昵称区分
        return "🤖", "DSH · AI"
    return avatar, nick


def json_lines(messages: list[dict], room_id: str, now_ms: int | None = None) -> Iterator[str]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    yield "{\n"
    yield f'  "room_id": {json.dumps(room_id, ensure_ascii=False)},\n'
    yield f'  "exported_at": {json.dumps(_iso(now), ensure_ascii=False)},\n'
    yield f'  "message_count": {len(messages)},\n'
    yield '  "messages": [\n'
    last = len(messages) - 1
    for i, m in enumerate(messages):
        item = {**m, "created_at_iso": _iso(m["created_at"])}
        sep = "," if i < last else ""
        yield "    " + json.dumps(item, ensure_ascii=False) + sep + "\n"
    yield "  ]\n}\n"


def markdown_lines(messages: list[dict], room_id: str, now_ms: int | None = None) -> Iterator[str]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    yield "# DSH Chat 聊天记录\n\n"
    yield f"- 房间：`{room_id}`\n"
    yield f"- 导出时间：{_iso(now)}\n"
    yield f"- 消息数：{len(messages)}\n\n"
    if not messages:
        yield "_（暂无消息）_\n"
        return
    yield "---\n\n"
    last_day = None
    for m in messages:
        dt = _local(m["created_at"])
        day = dt.strftime("%Y-%m-%d")
        if day != last_day:
            yield f"## {day}\n\n"
            last_day = day
        avatar, nick = _display_name(m)
        yield f"**{avatar} {_md_escape(nick)}** · {dt.strftime('%H:%M')}\n\n"
        for line in (m.get("text") or "").split("\n"):
            yield f"> {line}\n"
        yield "\n"


def filename(fmt: str, room_id: str, now_ms: int | None = None) -> str:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    stamp = _local(now).strftime("%Y%m%d-%H%M%S")
    safe_room = re.sub(r"[^A-Za-z0-9_-]", "-", room_id) or "room"
    return f"dsh-chat-{safe_room}-{stamp}.{'json' if fmt == 'json' else 'md'}"
