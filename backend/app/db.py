"""原生 SQLite 数据层（aiosqlite，无 ORM）。

安全约定：
  - 所有 SQL 都是参数化语句（? 占位符），用户输入绝不拼进 SQL 文本；
  - 唯一涉及 LIKE 的搜索对 % _ \\ 做转义并配合 ESCAPE；
  - 排序字段、AI 参数 key 均来自服务端白名单，不接受客户端动态列名；
  - 密码哈希与令牌哈希分别存储，任何接口都不返回 password_hash / token_hash。
"""
from __future__ import annotations

import time

import aiosqlite

from .config import settings
from .policies import ai_param_defaults
from .schemas import Message
from .security import token_hash as _token_hash

DDL = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     TEXT NOT NULL DEFAULT 'main',
    sender_type TEXT NOT NULL CHECK(sender_type IN ('human','ai')),
    sender_id   TEXT,
    nickname    TEXT NOT NULL,
    avatar      TEXT,
    text        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room_time ON messages(room_id, created_at);

-- 账号：首个注册者自动成为群主；管理员可为零人，由群主任命
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    nickname      TEXT NOT NULL,
    avatar        TEXT,
    role          TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner','admin','member')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','kicked')),
    muted_until   INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

-- 会话：只存 sha256(token)，令牌原文仅客户端持有
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- AI 参数（仅群主可改；D5 调度器 / D8 适配层读取）
CREATE TABLE IF NOT EXISTS ai_params (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    updated_by  INTEGER
);

-- 群资料（公告：群主 / 管理员可改）
CREATE TABLE IF NOT EXISTS group_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  INTEGER NOT NULL,
    updated_by  INTEGER
);
"""

_PUBLIC_FIELDS = ("id", "username", "nickname", "avatar", "role", "status", "muted_until", "created_at")


def public_user(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: row.get(k) for k in _PUBLIC_FIELDS}


async def init_db() -> None:
    async with aiosqlite.connect(settings.sqlite_path) as db:
        await db.executescript(DDL)
        now = int(time.time() * 1000)
        for key, value in ai_param_defaults().items():
            await db.execute(
                "INSERT OR IGNORE INTO ai_params(key,value,updated_at,updated_by) VALUES (?,?,?,NULL)",
                (key, value, now),
            )
        await db.execute(
            "INSERT OR IGNORE INTO group_meta(key,value,updated_at,updated_by) VALUES ('announcement','',?,NULL)",
            (now,),
        )
        await db.commit()


async def _rows(sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(settings.sqlite_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def _one(sql: str, params: tuple = ()) -> dict | None:
    rows = await _rows(sql, params)
    return rows[0] if rows else None


async def _exec(sql: str, params: tuple = ()) -> int:
    async with aiosqlite.connect(settings.sqlite_path) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return cur.rowcount


# ---------------- 消息 ----------------

async def insert_message(
    *,
    room_id: str,
    sender_type: str,
    sender_id: str | None,
    nickname: str,
    avatar: str | None,
    text: str,
) -> dict:
    created = int(time.time() * 1000)
    async with aiosqlite.connect(settings.sqlite_path) as db:
        cur = await db.execute(
            "INSERT INTO messages "
            "(room_id, sender_type, sender_id, nickname, avatar, text, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (room_id, sender_type, sender_id, nickname, avatar, text, created),
        )
        await db.commit()
        mid = cur.lastrowid
    return Message(
        id=mid, room_id=room_id, sender_type=sender_type,
        sender_id=sender_id, nickname=nickname, avatar=avatar,
        text=text, created_at=created,
    ).model_dump()


async def get_history(room_id: str, limit: int = 200) -> list[dict]:
    return await _rows(
        "SELECT * FROM messages WHERE room_id=? ORDER BY created_at ASC, id ASC LIMIT ?",
        (room_id, limit),
    )


def _escape_like(keyword: str) -> str:
    """转义 LIKE 通配符，避免用户输入 '%' / '_' 时变成全表匹配。"""
    return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_messages(room_id: str, keyword: str, limit: int = 50) -> list[dict]:
    pattern = f"%{_escape_like(keyword)}%"
    return await _rows(
        "SELECT * FROM messages WHERE room_id=? AND text LIKE ? ESCAPE '\\' "
        "ORDER BY created_at DESC, id DESC LIMIT ?",
        (room_id, pattern, limit + 1),
    )


async def get_all_messages(room_id: str, limit: int = 50000) -> list[dict]:
    return await _rows(
        "SELECT * FROM messages WHERE room_id=? ORDER BY created_at ASC, id ASC LIMIT ?",
        (room_id, limit),
    )


async def get_message(message_id: int) -> dict | None:
    return await _one("SELECT * FROM messages WHERE id=?", (message_id,))


async def delete_message(message_id: int) -> bool:
    """撤回 = 物理删除（原型）：历史、搜索、导出立即不再包含该条。"""
    return (await _exec("DELETE FROM messages WHERE id=?", (message_id,))) > 0


# ---------------- 账号 ----------------

async def count_users() -> int:
    row = await _one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


async def get_user_by_username(username: str) -> dict | None:
    return await _one("SELECT * FROM users WHERE username=?", (username,))


async def get_user_by_id(user_id: int) -> dict | None:
    return await _one("SELECT * FROM users WHERE id=?", (user_id,))


async def list_users() -> list[dict]:
    rows = await _rows("SELECT * FROM users ORDER BY id ASC")
    order = {"owner": 0, "admin": 1, "member": 2}
    return [public_user(r) for r in sorted(rows, key=lambda r: (order.get(r["role"], 9), r["id"]))]


async def create_user(username: str, password_hash: str, nickname: str, avatar: str) -> dict:
    """首个注册者自动成为群主。BEGIN IMMEDIATE 保证并发注册时只有一个 owner。"""
    now = int(time.time() * 1000)
    async with aiosqlite.connect(settings.sqlite_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT COUNT(*) FROM users")
            count = (await cur.fetchone())[0]
            role = "owner" if count == 0 else "member"
            cur = await db.execute(
                "INSERT INTO users "
                "(username,password_hash,nickname,avatar,role,status,muted_until,created_at) "
                "VALUES (?,?,?,?,?,'active',0,?)",
                (username, password_hash, nickname, avatar, role, now),
            )
            uid = cur.lastrowid
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return public_user(await get_user_by_id(uid))


async def update_user_profile(user_id: int, nickname: str, avatar: str) -> None:
    await _exec("UPDATE users SET nickname=?, avatar=? WHERE id=?", (nickname, avatar, user_id))


async def update_password_hash(user_id: int, password_hash: str) -> None:
    await _exec("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))


async def set_user_role(user_id: int, role: str) -> None:
    await _exec("UPDATE users SET role=? WHERE id=?", (role, user_id))


async def set_user_muted_until(user_id: int, until_ms: int) -> None:
    await _exec("UPDATE users SET muted_until=? WHERE id=?", (until_ms, user_id))


async def set_user_status(user_id: int, status: str) -> None:
    await _exec("UPDATE users SET status=? WHERE id=?", (status, user_id))


# ---------------- 会话 ----------------

async def create_session(token: str, user_id: int, expires_at: int) -> str:
    th = _token_hash(token)
    await _exec(
        "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?) "
        "ON CONFLICT(token_hash) DO UPDATE SET expires_at=excluded.expires_at",
        (th, user_id, int(time.time() * 1000), expires_at),
    )
    return th


async def resolve_token(token: str) -> dict | None:
    """令牌 -> 有效账号；过期自动清理，被踢账号直接失效。"""
    th = _token_hash(token)
    now = int(time.time() * 1000)
    async with aiosqlite.connect(settings.sqlite_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT u.*, s.expires_at AS _expires FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
            (th,),
        )
        row = await cur.fetchone()
        if row and int(row["_expires"]) <= now:
            await db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            await db.commit()
            return None
        if row and row["status"] != "active":
            await db.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
            await db.commit()
            return None
    return public_user(dict(row)) if row else None


async def session_exists(th: str) -> bool:
    row = await _one(
        "SELECT 1 AS ok FROM sessions WHERE token_hash=? AND expires_at>?",
        (th, int(time.time() * 1000)),
    )
    return row is not None


async def delete_session(token: str | None) -> None:
    if token:
        await _exec("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


async def delete_user_sessions(user_id: int) -> None:
    await _exec("DELETE FROM sessions WHERE user_id=?", (user_id,))


# ---------------- AI 参数 / 群资料 ----------------

async def get_ai_params() -> dict[str, str]:
    rows = await _rows("SELECT key,value FROM ai_params")
    merged = ai_param_defaults()
    merged.update({r["key"]: r["value"] for r in rows})
    return merged


async def set_ai_params(values: dict[str, str], user_id: int) -> None:
    now = int(time.time() * 1000)
    async with aiosqlite.connect(settings.sqlite_path) as db:
        for key, value in values.items():  # key 已由 policies 白名单校验
            await db.execute(
                "INSERT INTO ai_params(key,value,updated_at,updated_by) VALUES (?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (key, value, now, user_id),
            )
        await db.commit()


async def get_meta(key: str) -> str:
    row = await _one("SELECT value FROM group_meta WHERE key=?", (key,))
    return row["value"] if row else ""


async def set_meta(key: str, value: str, user_id: int) -> None:
    await _exec(
        "INSERT INTO group_meta(key,value,updated_at,updated_by) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value, int(time.time() * 1000), user_id),
    )
