"""Socket.IO 事件：所有实时操作都先过账号鉴权与权限校验。

关键安全点：
  - join 只认服务端签发的令牌，身份（昵称/头像/角色/ID）一律取自数据库，
    客户端自报字段全部忽略，杜绝伪造他人身份；
  - message 每次都回查账号状态、会话有效性、禁言状态，避免角色变更后旧连接继续生效；
  - sender_id 使用稳定账号 ID，重新登录/换设备后自己的历史消息仍显示在右侧。
"""
from __future__ import annotations

import asyncio
import time

from . import db, events, state
from .config import settings
from .security import token_hash, validate_text


def register_handlers(sio) -> None:
    @sio.event
    async def connect(sid, environ, auth):
        # 连接本身不授信；必须随后用 join(token) 完成鉴权才能收发消息
        pass

    @sio.event
    async def join(sid, data):
        token = (data or {}).get("token") if isinstance(data, dict) else None
        if not isinstance(token, str) or not token or len(token) > 256:
            await sio.emit("auth:invalid", {"reason": "缺少登录令牌"}, to=sid)
            return
        user = await db.resolve_token(token)
        if not user:
            await sio.emit("auth:invalid", {"reason": "登录已失效，请重新登录"}, to=sid)
            return

        state.put(sid, user, token_hash(token))
        history = await db.get_history(settings.default_room)
        announcement = await db.get_meta("announcement")
        await sio.emit(
            "joined",
            {
                "session_id": str(user["id"]),
                "user": user,
                "announcement": announcement,
            },
            to=sid,
        )
        await sio.emit("history", {"messages": history}, to=sid)
        await events.presence()
        await events.members_changed()

    @sio.event
    async def message(sid, data):
        sess = state.SESSIONS.get(sid)
        if not sess:
            # 竞态兜底：弱网/首次加载时，客户端可能在 socket 刚连上、join 还在路上就发出
            # 第一条消息。此时直接判「未登录」会把用户直接踢回登录页（体验极差）。
            # 这里给 join 最多 1.5s 的宽限，仍未完成才回一条普通错误，不注销会话。
            for _ in range(30):
                await asyncio.sleep(0.05)
                sess = state.SESSIONS.get(sid)
                if sess:
                    break
        if not sess:
            await sio.emit(
                "compose:error",
                {"message": "连接还没就绪，稍等一下再发"},
                to=sid,
            )
            return

        uid = sess["user"]["id"]
        user = await db.get_user_by_id(uid)
        if (
            not user
            or user["status"] != "active"
            or not await db.session_exists(sess["token_hash"])
        ):
            await sio.emit("auth:invalid", {"reason": "登录已失效，请重新登录"}, to=sid)
            await sio.disconnect(sid=sid)
            return

        if int(user["muted_until"]) > int(time.time() * 1000):
            await sio.emit("compose:error", {"message": "你已被禁言，暂时无法发言"}, to=sid)
            return

        try:
            text = validate_text((data or {}).get("text"), settings.max_message_chars)
        except ValueError as exc:
            await sio.emit("compose:error", {"message": str(exc)}, to=sid)
            return

        msg = await db.insert_message(
            room_id=settings.default_room,
            sender_type="human",
            sender_id=str(uid),
            nickname=user["nickname"],
            avatar=user["avatar"],
            text=text,
        )
        await sio.emit("message:new", msg)

        # D5–D7：人类消息入站后交给调度器做便宜的叫醒判断（推理在后台任务，不阻塞本链路）
        if state.scheduler is not None:
            await state.scheduler.on_human_message(msg)

    @sio.event
    async def disconnect(sid):
        state.pop(sid)
        await events.presence()
