"""关键词搜索服务（D3）。

原型用 SQLite LIKE：单房间、N<=20、消息量小，全表扫描完全够用。
未来升级 FTS5 时只改这里（改为查 fts_messages 虚拟表），表结构与 API 不动。
"""

from __future__ import annotations

from .config import settings
from .db import search_messages

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


async def search(keyword: str, limit: int = DEFAULT_LIMIT) -> dict:
    """返回 {query, messages, has_more}；结果按时间倒序（最新在前）。"""
    q = keyword.strip()
    if not q:
        return {"query": "", "messages": [], "has_more": False}
    limit = max(1, min(limit, MAX_LIMIT))
    rows = await search_messages(settings.default_room, q, limit)
    has_more = len(rows) > limit
    return {"query": q, "messages": rows[:limit], "has_more": has_more}
