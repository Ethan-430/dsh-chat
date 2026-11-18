"""REST 接口（D3–D4 + 账号/权限体系）。

同源挂在 FastAPI 的 8000 端口；必须在 mount("/") 静态托管之前注册，
否则 catch-all 的 StaticFiles 会吞掉 /api/* 与 /socket.io。

安全约定：
  - 除 /api/auth/register|login 外，全部接口需 Authorization: Bearer <token>；
  - 所有管理动作在服务端用 policies 复核角色，前端只负责隐藏按钮；
  - 所有 SQL 参数化；输入经 security 的白名单校验；未知参数一律拒绝。
"""
from __future__ import annotations

import re
import sqlite3
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from . import db, events, export_service, search_service, state
from .auth import current_session, current_user
from .config import settings
from .policies import (
    AI_PARAM_LABELS,
    AI_PARAM_SPEC,
    ROLE_LABEL,
    can_moderate,
    can_recall,
    is_owner,
    is_staff,
    validate_ai_params,
)
from .security import (
    LoginLimiter,
    dummy_verify,
    hash_password,
    new_token,
    token_hash,
    validate_avatar,
    validate_nickname,
    validate_password,
    validate_text,
    validate_username,
    verify_password,
)

router = APIRouter(prefix="/api")
limiter = LoginLimiter(max_fail=settings.login_max_fail, window_sec=settings.login_window_sec)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------- 通用小工具 ----------

def _body(payload: dict | None) -> dict:
    return payload if isinstance(payload, dict) else {}


def _as_int(value, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HTTPException(status_code=400, detail=f"{field} 格式不正确")
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} 格式不正确") from exc


def _require_owner(user: dict) -> None:
    if not is_owner(user):
        raise HTTPException(status_code=403, detail="仅群主可执行此操作")


def _require_staff(user: dict) -> None:
    if not is_staff(user):
        raise HTTPException(status_code=403, detail="仅群主或管理员可执行此操作")


def _ok(**extra):
    return {"ok": True, **extra}


async def _refresh_sessions(user_ids: set[int] | None = None) -> None:
    """角色/资料变更后刷新进程内会话快照，避免旧角色残留。"""
    for sid, sess in list(state.SESSIONS.items()):
        uid = sess["user"]["id"]
        if user_ids is None or uid in user_ids:
            fresh = await db.get_user_by_id(uid)
            if fresh:
                sess["user"] = db.public_user(fresh)


async def _issue_token(user_id: int) -> str:
    token = new_token()
    expires = int(time.time() * 1000) + settings.session_ttl_hours * 3600 * 1000
    await db.create_session(token, user_id, expires)
    return token


# ---------- 认证 ----------

@router.get("/auth/status")
async def auth_status():
    """公开状态：供登录页提示「首个注册者将成为群主」。不泄露任何账号信息。"""
    users = await db.list_users()
    return {
        "user_count": len(users),
        "has_owner": any(u["role"] == "owner" and u["status"] == "active" for u in users),
    }


@router.post("/auth/register")
async def register(payload: dict | None = Body(default=None)):
    """首个注册账号自动成为群主；其余为普通成员（管理员由群主手动任命）。"""
    p = _body(payload)
    try:
        username = validate_username(p.get("username"))
        password = validate_password(p.get("password"))
        nickname = validate_nickname(p.get("nickname"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    avatar = validate_avatar(p.get("avatar"))

    if await db.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="该用户名已被注册")
    try:
        user = await db.create_user(username, hash_password(password), nickname, avatar)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="该用户名已被注册") from exc

    token = await _issue_token(user["id"])
    await events.members_changed()
    return {"token": token, "user": user, "role_label": ROLE_LABEL[user["role"]]}


@router.post("/auth/login")
async def login(request: Request, payload: dict | None = Body(default=None)):
    p = _body(payload)
    username = (p.get("username") if isinstance(p.get("username"), str) else "").strip()
    password = p.get("password")
    if not isinstance(password, str):
        raise HTTPException(status_code=400, detail="请输入密码")

    ip = request.client.host if request.client else "unknown"
    key = LoginLimiter.key(username, ip)
    try:
        await limiter.check(key)
    except PermissionError as exc:
        raise HTTPException(
            status_code=429, detail=str(exc),
            headers={"Retry-After": str(settings.login_window_sec)},
        ) from exc

    row = await db.get_user_by_username(username)
    if not row:
        dummy_verify(password)  # 抹平「用户不存在」与「密码错误」的时序差
        await limiter.record_fail(key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not verify_password(password, row["password_hash"]):
        await limiter.record_fail(key)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if row["status"] != "active":
        await limiter.record_fail(key)
        raise HTTPException(status_code=403, detail="该账号已被移出群聊")

    await limiter.reset(key)
    token = await _issue_token(row["id"])
    return {"token": token, "user": db.public_user(row), "role_label": ROLE_LABEL[row["role"]]}


@router.post("/auth/logout")
async def logout(session=Depends(current_session)):
    _user, token = session
    await db.delete_session(token)
    await events.force_logout_token(token_hash(token), "已退出登录")
    return _ok()


@router.get("/auth/me")
async def me(user: dict = Depends(current_user)):
    return {
        "user": user,
        "announcement": await db.get_meta("announcement"),
        "role_label": ROLE_LABEL.get(user["role"], "成员"),
    }


@router.post("/auth/password")
async def change_password(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """改密后该账号所有旧会话立即失效，返回一枚新令牌给当前客户端。"""
    p = _body(payload)
    old = p.get("old_password")
    if not isinstance(old, str):
        raise HTTPException(status_code=400, detail="请输入原密码")
    try:
        new = validate_password(p.get("new_password"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    row = await db.get_user_by_id(user["id"])
    if not row or not verify_password(old, row["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码不正确")
    await db.update_password_hash(user["id"], hash_password(new))
    await db.delete_user_sessions(user["id"])  # 其余设备 / 旧令牌立刻失效
    token = await _issue_token(user["id"])
    return {"token": token, "user": db.public_user(await db.get_user_by_id(user["id"]))}


@router.patch("/me")
async def update_profile(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    p = _body(payload)
    try:
        nickname = validate_nickname(p.get("nickname"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    avatar = validate_avatar(p.get("avatar"))
    await db.update_user_profile(user["id"], nickname, avatar)
    await _refresh_sessions({user["id"]})
    await events.members_changed()
    return {"user": db.public_user(await db.get_user_by_id(user["id"]))}


# ---------- 群成员 / 群资料 ----------

@router.get("/members")
async def members(user: dict = Depends(current_user)):
    return {
        "members": await db.list_users(),
        "online_ids": state.online_ids(),
        "announcement": await db.get_meta("announcement"),
        "me": user,
    }


async def _target(user: dict, payload: dict) -> dict:
    target_id = _as_int(payload.get("user_id"), "user_id")
    target = await db.get_user_by_id(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not can_moderate(user, target):
        raise HTTPException(status_code=403, detail="无权管理该成员")
    return target


@router.post("/admin/role")
async def set_role(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """仅群主：把普通成员设为管理员，或撤消管理员。管理员人数可以为 0。"""
    _require_owner(user)
    p = _body(payload)
    role = p.get("role")
    if role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role 只能是 admin 或 member")
    target_id = _as_int(p.get("user_id"), "user_id")
    target = await db.get_user_by_id(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target["role"] == "owner" or target["id"] == user["id"]:
        raise HTTPException(status_code=400, detail="群主角色不可更改")

    await db.set_user_role(target_id, role)
    await _refresh_sessions({target_id})
    await events.members_changed()
    await events.notice(
        f"{user['nickname']} 将 {target['nickname']} 设为{ROLE_LABEL[role]}"
        if role == "admin" else
        f"{user['nickname']} 撤消了 {target['nickname']} 的管理员"
    )
    return _ok(role=role, user_id=target_id)


@router.post("/admin/mute")
async def mute(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """群主/管理员禁言；minutes = 0 表示解除禁言。"""
    _require_staff(user)
    p = _body(payload)
    target = await _target(user, p)
    minutes = _as_int(p.get("minutes", 0), "minutes")
    if not (0 <= minutes <= settings.mute_max_minutes):
        raise HTTPException(status_code=400, detail=f"禁言时长需在 0–{settings.mute_max_minutes} 分钟")
    until = 0 if minutes == 0 else int(time.time() * 1000) + minutes * 60_000
    await db.set_user_muted_until(target["id"], until)
    await _refresh_sessions({target["id"]})
    await events.members_changed()
    await events.notice(
        f"{target['nickname']} 被 {user['nickname']} 禁言 {minutes} 分钟" if minutes
        else f"{target['nickname']} 的禁言已解除"
    )
    return _ok(muted_until=until)


@router.post("/admin/kick")
async def kick(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """踢出：账号标记 kicked、吊销全部会话、断开其所有实时连接。"""
    _require_staff(user)
    target = await _target(user, _body(payload))
    await db.set_user_status(target["id"], "kicked")
    await db.delete_user_sessions(target["id"])
    await events.force_logout(target["id"], "你已被移出群聊")
    await events.members_changed()
    await events.notice(f"{target['nickname']} 已被 {user['nickname']} 移出群聊", "warn")
    return _ok()


@router.post("/admin/restore")
async def restore(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """仅群主：把被踢出的成员恢复入群（相当于重新邀请）。"""
    _require_owner(user)
    target_id = _as_int(_body(payload).get("user_id"), "user_id")
    target = await db.get_user_by_id(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    await db.set_user_status(target_id, "active")
    await db.set_user_muted_until(target_id, 0)
    await events.members_changed()
    await events.notice(f"{user['nickname']} 将 {target['nickname']} 重新邀请入群")
    return _ok()


@router.put("/group/announcement")
async def announcement(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    """群主/管理员可编辑群公告；普通成员只读。"""
    _require_staff(user)
    raw = _body(payload).get("text", "")
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="公告格式不正确")
    try:
        text = "" if not raw.strip() else validate_text(raw, settings.max_announcement_chars)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if _CONTROL_RE.search(text.replace("\n", "")):
        raise HTTPException(status_code=400, detail="公告不能包含控制字符")
    await db.set_meta("announcement", text, user["id"])
    await state.sio.emit("announcement", {"text": text, "by": user["nickname"]})
    await events.notice(f"{user['nickname']} 更新了群公告")
    return _ok(text=text)


# ---------- AI 参数（仅群主可改） ----------

@router.get("/ai/params")
async def get_ai_params(user: dict = Depends(current_user)):
    return {
        "params": await db.get_ai_params(),
        "editable": is_owner(user),
        "spec": {
            key: {
                "type": spec[0], "min": spec[1], "max": spec[2],
                "default": spec[3], "label": AI_PARAM_LABELS[key],
            }
            for key, spec in AI_PARAM_SPEC.items()
        },
    }


@router.get("/ai/status")
async def ai_status(user: dict = Depends(current_user)):
    """D5–D7 调度器运行状态（只读）：适配器、冷却计数、当日调用、最近唤醒决策。

    参数每次唤醒都从 ai_params 表热读，这里展示的是同一份数据 + Redis 状态快照。
    """
    sched = state.scheduler
    if sched is None:
        return {"ready": False, "adapter": "none", "reason": "调度器未装配"}
    return await sched.status()


@router.put("/ai/params")
async def put_ai_params(
    payload: dict | None = Body(default=None),
    user: dict = Depends(current_user),
):
    _require_owner(user)
    p = _body(payload)
    raw = p.get("params") if isinstance(p.get("params"), dict) else p
    try:
        values = validate_ai_params(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.set_ai_params(values, user["id"])
    await events.notice(f"群主 {user['nickname']} 调整了 AI 参数")
    return {"params": await db.get_ai_params(), "ok": True}


# ---------- 消息撤回 ----------

@router.delete("/messages/{message_id}")
async def recall_message(message_id: int, user: dict = Depends(current_user)):
    msg = await db.get_message(message_id)
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")
    sender = None
    if msg["sender_type"] == "human" and msg["sender_id"]:
        try:
            sender = await db.get_user_by_id(int(msg["sender_id"]))
        except (ValueError, TypeError):
            sender = None  # 兼容清空前的旧匿名数据
    if not can_recall(user, msg, sender, int(time.time() * 1000), settings.recall_window_ms):
        raise HTTPException(status_code=403, detail="无权撤回该消息（或已超过 2 分钟）")
    await db.delete_message(message_id)
    await events.recalled(message_id, user["nickname"])
    return _ok()


# ---------- D3–D4：搜索 / 导出（现需登录） ----------

@router.get("/search")
async def search(
    response: Response,
    q: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(current_user),
):
    """GET /api/search?q=关键词 —— 正文子串匹配，结果按时间倒序。"""
    response.headers["Cache-Control"] = "no-store"
    return await search_service.search(q, limit)


@router.get("/export")
async def export_messages(
    format: str = "json",
    user: dict = Depends(current_user),
):
    """GET /api/export?format=json|md —— 附件形式流式下载全量聊天记录。"""
    if format not in ("json", "md"):
        raise HTTPException(status_code=400, detail="format must be 'json' or 'md'")

    messages = await db.get_all_messages(settings.default_room)
    now_ms = int(time.time() * 1000)
    room = settings.default_room

    if format == "json":
        generator = export_service.json_lines(messages, room, now_ms)
        media_type = "application/json; charset=utf-8"
    else:
        generator = export_service.markdown_lines(messages, room, now_ms)
        media_type = "text/markdown; charset=utf-8"

    headers = {
        "Content-Disposition": f'attachment; filename="{export_service.filename(format, room, now_ms)}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(generator, media_type=media_type, headers=headers)
