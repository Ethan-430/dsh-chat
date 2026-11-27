"""语音合成调度：云端把活推给本机 worker，超时则降级为文字。

失败语义（都指向同一结果：这条消息就当普通文字消息看）：
  - worker 不在线         -> 2 秒宽限后立刻 failed（不必让用户等 15 秒）
  - 合成超时              -> failed
  - worker 明确回报失败   -> failed（worker 也会直接回调 /fail，先到先算）
失败后前端收到 message:audio{status:failed}，气泡原地变成文字 + 一行小字标记。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import db, state
from .config import settings

log = logging.getLogger("dsh.tts")

_WORKER_GRACE_S = 2.0


async def _emit(sio: Any, event: str, payload: dict) -> None:
    if sio is None:
        return
    try:
        await asyncio.wait_for(sio.emit(event, payload), timeout=3)
    except Exception:
        log.exception("广播 %s 失败", event)


async def mark_failed(sio: Any, message_id: int, reason: str) -> None:
    try:
        await db.set_message_audio(message_id, status="failed")
    except Exception:
        log.exception("标记语音失败失败 id=%s", message_id)
    await _emit(sio, "message:audio", {"id": message_id, "audio_status": "failed"})
    log.info("语音降级为文字 id=%s reason=%s", message_id, reason)


async def request_synthesis(sio: Any, *, message_id: int, text: str) -> None:
    """后台任务：把一条消息的文本交给 worker 合成。

    成功时由 worker 通过 HTTP PUT /api/voice/worker/{id} 回传音频，服务端落盘后
    自己广播 message:audio；这里只负责「把活递出去」和「判断失败」。
    """
    if not settings.voice_enabled:
        await mark_failed(sio, message_id, "voice_disabled")
        return
    body = (text or "").strip()
    if not body:
        await mark_failed(sio, message_id, "empty_text")
        return
    if len(body) > settings.voice_max_chars:
        await mark_failed(sio, message_id, "too_long")
        return

    # worker 可能刚好在重连：先给一个很短的宽限，避免网络抖动导致误降级
    deadline = asyncio.get_event_loop().time() + _WORKER_GRACE_S
    sid = state.pick_worker()
    while not sid and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        sid = state.pick_worker()
    if not sid:
        await mark_failed(sio, message_id, "no_worker:")
        return

    payload = {"id": message_id, "text": body, "room": settings.default_room}
    try:
        ack = await asyncio.wait_for(
            sio.call("tts:job", payload, to=sid, namespace="/worker",
                     timeout=settings.voice_timeout_s),
            timeout=settings.voice_timeout_s + 1,
        )
    except asyncio.TimeoutError:
        await mark_failed(sio, message_id, "timeout")
        return
    except Exception as exc:
        await mark_failed(sio, message_id, f"worker_error:{type(exc).__name__}")
        return

    if isinstance(ack, dict) and not ack.get("ok", True):
        await mark_failed(sio, message_id, f"worker_refused:{ack.get('error')}")
        return

    # ack 只代表 worker 完成了上传；真正落盘由 HTTP 路由负责，这里只做兜底校验
    row = await db.get_message(message_id)
    if row and row.get("audio_status") == "pending":
        await mark_failed(sio, message_id, "ack_without_upload")


async def fail_stale_pending() -> None:
    """进程重启后，上一轮没合成完的 pending 全部作废，避免气泡一直转圈。"""
    try:
        rows = await db._rows(  # noqa: SLF001 - 同包内部工具，避免为此新增公共 API
            "SELECT id FROM messages WHERE audio_status='pending'"
        )
        for r in rows:
            await db.set_message_audio(int(r["id"]), status="failed")
        if rows:
            log.info("清理未完成的语音消息 %d 条", len(rows))
    except Exception:
        log.exception("清理 pending 语音失败")
