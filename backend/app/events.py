"""统一的服务端广播，供 Socket.IO 处理器与 REST 管理接口共用。"""
from __future__ import annotations

from . import state


async def presence() -> None:
    await state.sio.emit(
        "presence",
        {"online_count": len(state.SESSIONS), "online_ids": state.online_ids()},
    )


async def members_changed() -> None:
    await state.sio.emit("members:changed", {})


async def notice(text: str, level: str = "info") -> None:
    await state.sio.emit("system:notice", {"text": text, "level": level})


async def recalled(message_id: int, by: str) -> None:
    await state.sio.emit("message:recalled", {"id": message_id, "by": by})


async def _drop(sids: list[str], reason: str) -> None:
    for sid in sids:
        await state.sio.emit("auth:invalid", {"reason": reason}, to=sid)
        await state.sio.disconnect(sid=sid)
    await presence()


async def force_logout(user_id: int, reason: str) -> None:
    """被踢出：通知该账号的所有连接清 token 并断开。"""
    await _drop(state.sids_of(user_id), reason)


async def force_logout_token(token_hash: str, reason: str) -> None:
    """单个会话失效（登出）：只断开持有该令牌的连接，不影响其他设备。"""
    sids = [sid for sid, s in state.SESSIONS.items() if s["token_hash"] == token_hash]
    await _drop(sids, reason)
