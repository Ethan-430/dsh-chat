"""进程内实时会话状态（sid <-> 账号）。

敏感权限每次都回查数据库，这里只保存「这个连接是谁」的索引，避免角色/封禁变更后
旧状态继续生效。sio 由 main.py 在装配时注入，供 REST 侧广播使用。
"""
from __future__ import annotations

from typing import Any

sio: Any = None

# D5–D7 发言调度器，由 main.py 在 lifespan 中装配（chat_router / REST 读取）
scheduler: Any = None

# sid -> {"user": public_user dict, "token_hash": str}
SESSIONS: dict[str, dict] = {}

# 语音合成 worker（本机常驻进程，反向连进来领活）：sid -> {"since": ms, "name": str}
WORKERS: dict[str, dict] = {}


def worker_add(sid: str, info: dict | None = None) -> None:
    WORKERS[sid] = dict(info or {})


def worker_pop(sid: str) -> None:
    WORKERS.pop(sid, None)


def pick_worker() -> str | None:
    """目前只会有 1 个 worker；多个时取最早连上的那个（避免任务来回跳）。"""
    if not WORKERS:
        return None
    return next(iter(WORKERS))


def attach(server) -> None:
    global sio
    sio = server


def attach_scheduler(instance) -> None:
    global scheduler
    scheduler = instance


def put(sid: str, user: dict, token_hash: str) -> None:
    SESSIONS[sid] = {"user": user, "token_hash": token_hash}


def pop(sid: str) -> None:
    SESSIONS.pop(sid, None)


def online_ids() -> list[int]:
    return sorted({s["user"]["id"] for s in SESSIONS.values()})


def sids_of(user_id: int) -> list[str]:
    return [sid for sid, s in SESSIONS.items() if s["user"]["id"] == user_id]
