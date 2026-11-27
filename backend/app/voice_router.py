"""语音接口：播放、人类录音上传、本机 worker 回传合成音频。

设计要点：
  - 播放走 GET /api/voice/{id}，支持 Range；<audio> 标签不能带自定义头，
    所以这条路由额外接受 ?token=（仅此一条，且同样实时回查会话）。
  - 人类录音用「原始 body + Content-Type」上传，不引 python-multipart：
    依赖越少，线上越不容易因为少装一个包而 500。
  - worker 回传用 PUT，靠 X-Worker-Token 鉴权（与 settings.dsh_worker_token 比对），
    语义上它是「服务端代理」，不绑定任何账号会话。
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import time

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import db, state, voice
from .auth import _pick_token
from .config import settings

log = logging.getLogger("dsh.voice")

router = APIRouter(prefix="/api/voice")


def _require_worker(token: str | None) -> None:
    expected = settings.dsh_worker_token or ""
    if not expected:
        raise HTTPException(status_code=403, detail="服务端未启用 worker")
    given = (token or "").strip()
    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="worker 令牌无效")


async def _user_from_any(token: str | None, x_dsh_token: str | None, authorization: str | None) -> dict:
    picked = _pick_token(x_dsh_token, authorization) or (token or "").strip() or None
    if not picked or len(picked) > 256:
        raise HTTPException(status_code=401, detail="请先登录")
    user = await db.resolve_token(picked)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


async def _broadcast(event: str, payload: dict) -> None:
    sio = state.sio
    if sio is None:
        return
    try:
        await sio.emit(event, payload)
    except Exception:
        log.exception("广播 %s 失败", event)


# ---------------- 状态 ----------------

# 注意：必须注册在 GET /{message_id} 之前，否则 "status" 会被当作 message_id
# 去做 int 解析，路由被遮蔽后永远 422。
@router.get("/status")
async def voice_status(
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
):
    await _user_from_any(None, x_dsh_token, authorization)
    ffmpeg = voice.find_ffmpeg()
    return {
        "enabled": settings.voice_enabled,
        "worker_configured": bool(settings.dsh_worker_token),
        "workers_online": len(state.WORKERS),
        "ffmpeg": os.path.basename(ffmpeg) if ffmpeg else None,
        "dir": voice.base_dir(),
        "max_chars": settings.voice_max_chars,
        "max_ms": settings.voice_max_ms,
        "probe": secrets.token_hex(4),  # 便于区分缓存
    }


# ---------------- 播放 ----------------

@router.get("/{message_id}")
async def get_audio(
    message_id: int,
    request: Request,
    token: str | None = Query(default=None, max_length=256),
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
):
    await _user_from_any(token, x_dsh_token, authorization)
    row = await db.get_message(message_id)
    if not row or row.get("audio_status") != "ready":
        raise HTTPException(status_code=404, detail="语音不存在或尚未合成")
    path = voice.resolve(row.get("audio_path"))
    if not path:
        raise HTTPException(status_code=404, detail="语音文件已丢失")
    mime = row.get("audio_mime") or "audio/mpeg"
    try:
        return voice.audio_response(path, request, mime)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="语音文件为空")


@router.get("/{message_id}/meta")
async def audio_meta(
    message_id: int,
    token: str | None = Query(default=None, max_length=256),
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
):
    """轻量元信息（排障用）：前端不依赖它播放。"""
    await _user_from_any(token, x_dsh_token, authorization)
    row = await db.get_message(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="消息不存在")
    return {
        "id": row["id"],
        "audio_status": row.get("audio_status"),
        "audio_duration_ms": row.get("audio_duration_ms") or 0,
        "audio_mime": row.get("audio_mime"),
    }


# ---------------- 人类录音上传 ----------------

@router.post("/upload")
async def upload_audio(
    request: Request,
    duration_ms: int = Query(default=0, ge=0, le=600_000),
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
):
    if not settings.voice_enabled:
        raise HTTPException(status_code=403, detail="语音功能未启用")
    user = await _user_from_any(None, x_dsh_token, authorization)
    if int(user.get("muted_until") or 0) > int(time.time() * 1000):
        raise HTTPException(status_code=403, detail="你已被禁言，暂时无法发言")

    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = voice.ALLOWED_MIME.get(mime)
    if not ext:
        raise HTTPException(status_code=415, detail=f"不支持的音频格式：{mime or '未知'}")

    declared = request.headers.get("content-length")
    if declared and int(declared) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="录音太大了")

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="录音是空的")
    if len(body) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="录音太大了")
    if duration_ms and duration_ms > settings.voice_max_ms + 2000:
        raise HTTPException(status_code=400, detail="单条语音不能超过 60 秒")

    msg = await db.insert_message(
        room_id=settings.default_room,
        sender_type="human",
        sender_id=str(user["id"]),
        nickname=user["nickname"],
        avatar=user["avatar"],
        text="",
        audio_status="pending",
        audio_duration_ms=duration_ms,
    )
    await _broadcast("message:new", msg)

    root = voice.ensure_dir()
    mid = int(msg["id"])
    raw_name = f"{mid}.raw{ext}"
    raw_path = os.path.join(root, raw_name)
    with open(raw_path, "wb") as fh:
        fh.write(body)

    final_name, final_mime = raw_name, mime
    mp3_name = f"{mid}.mp3"
    if voice.transcode_to_mp3(raw_path, os.path.join(root, mp3_name)):
        try:
            os.remove(raw_path)
        except OSError:
            pass
        final_name, final_mime = mp3_name, "audio/mpeg"

    final_path = os.path.join(root, final_name)
    got_ms = voice.probe_duration_ms(final_path) or duration_ms
    await db.set_message_audio(
        mid, status="ready", path=final_name, mime=final_mime, duration_ms=got_ms,
    )
    await _broadcast("message:audio", {
        "id": mid, "audio_status": "ready", "audio_duration_ms": got_ms,
    })

    fresh = await db.get_message(mid) or msg
    if state.scheduler is not None:
        try:
            await state.scheduler.on_human_message(fresh)
        except Exception:
            log.exception("语音消息入站调度失败（不影响发送）")
    return JSONResponse({"ok": True, "id": mid, "duration_ms": got_ms, "mime": final_mime})


# ---------------- worker 回传 ----------------

@router.put("/worker/{message_id}")
async def worker_push(
    message_id: int,
    request: Request,
    duration_ms: int = Query(default=0, ge=0),
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
):
    _require_worker(x_worker_token)
    row = await db.get_message(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="消息不存在")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="音频是空的")
    if len(body) > settings.voice_upload_max_bytes:
        raise HTTPException(status_code=413, detail="音频太大")

    mime = (request.headers.get("content-type") or "audio/mpeg").split(";")[0].strip().lower()
    ext = ".mp3" if mime == "audio/mpeg" else (voice.ALLOWED_MIME.get(mime) or ".wav")
    root = voice.ensure_dir()
    name = f"{message_id}{ext}"
    with open(os.path.join(root, name), "wb") as fh:
        fh.write(body)

    got_ms = voice.probe_duration_ms(os.path.join(root, name)) or duration_ms
    if got_ms > settings.voice_max_ms + 5000:
        log.warning("合成音频超出时长上限：%s ms", got_ms)
    await db.set_message_audio(
        message_id, status="ready", path=name, mime=mime, duration_ms=got_ms,
    )
    await _broadcast("message:audio", {
        "id": message_id, "audio_status": "ready", "audio_duration_ms": got_ms,
    })
    return {"ok": True, "duration_ms": got_ms}


@router.post("/worker/{message_id}/fail")
async def worker_fail(
    message_id: int,
    request: Request,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
):
    """worker 明确失败时的回调：立刻降级为文字，不用等 15 秒超时。"""
    _require_worker(x_worker_token)
    row = await db.get_message(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="消息不存在")
    await db.set_message_audio(message_id, status="failed")
    await _broadcast("message:audio", {"id": message_id, "audio_status": "failed"})
    return {"ok": True}

