"""/worker 命名空间：本机 TTS worker 的反向长连接。

为什么不用「云端主动连本机」：
    本机没有公网入口，开隧道要域名、要暴露端口，而且 URL 一变就失联。
    反过来让 worker 主动连云端，云端一有活就推下去——延迟 ~100 ms，
    且你关机期间的任务会堆在 pending 上，worker 回来后再补。

鉴权：worker 用 auth={"token": settings.dsh_worker_token} 连接，连接期校验一次；
      回传音频时还会再用 X-Worker-Token 验一次（两道闸，互不依赖）。
"""
from __future__ import annotations

import hmac
import logging
import time

from . import state
from .config import settings

log = logging.getLogger("dsh.worker")

NAMESPACE = "/worker"


def register_worker_namespace(sio) -> None:
    @sio.on("connect", namespace=NAMESPACE)
    async def connect(sid, environ, auth):
        expected = settings.dsh_worker_token or ""
        given = ""
        if isinstance(auth, dict):
            given = str(auth.get("token") or environ.get("HTTP_X_WORKER_TOKEN") or "")
        if not expected or not given or not hmac.compare_digest(given, expected):
            log.warning("worker 鉴权失败 sid=%s", sid)
            raise ConnectionRefusedError("worker token invalid")
        state.worker_add(sid, {"since": int(time.time() * 1000), "name": "tts-worker"})
        log.info("TTS worker 已连接 sid=%s 在线=%d", sid, len(state.WORKERS))
        await sio.emit("worker:welcome", {
            "max_chars": settings.voice_max_chars,
            "max_ms": settings.voice_max_ms,
            "timeout_s": settings.voice_timeout_s,
        }, to=sid, namespace=NAMESPACE)

    @sio.on("disconnect", namespace=NAMESPACE)
    async def disconnect(sid):
        state.worker_pop(sid)
        log.info("TTS worker 断开 sid=%s 在线=%d", sid, len(state.WORKERS))

    @sio.on("worker:hello", namespace=NAMESPACE)
    async def hello(sid, data):
        if sid in state.WORKERS:
            state.worker_add(sid, {**state.WORKERS[sid], "info": data if isinstance(data, dict) else {}})
        return {"ok": True, "server_time": int(time.time() * 1000)}

    @sio.on("worker:ping", namespace=NAMESPACE)
    async def ping(sid, data=None):
        return {"ok": True, "t": int(time.time() * 1000)}
