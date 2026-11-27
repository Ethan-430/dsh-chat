import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import socketio

from . import state, tts
from .chat_router import register_handlers
from .db import init_db
from .redis_client import redis
from .rest import router as rest_router
from .scheduler import DshScheduler
from .voice_router import router as voice_router
from .worker_router import register_worker_namespace


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await tts.fail_stale_pending()   # 上次进程留下的 pending 语音一律作废
    scheduler = DshScheduler(sio)
    await scheduler.start()
    state.attach_scheduler(scheduler)
    try:
        yield
    finally:
        await scheduler.stop()
        state.attach_scheduler(None)
        close = getattr(redis, "aclose", None) or getattr(redis, "close", None)
        if close:
            try:
                await close()
            except Exception:
                pass


fastapi_app = FastAPI(title="DSH-CHAT", lifespan=lifespan)


@fastapi_app.middleware("http")
async def api_no_store(request: Request, call_next):
    """所有 /api/* 响应禁止缓存：登录令牌、成员列表、导出都不应落浏览器缓存。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=["*"],
    max_http_buffer_size=8 * 1024 * 1024,   # worker 回传/大 payload 留余量
)
state.attach(sio)  # REST 管理接口需要借 sio 广播成员/在线/撤回事件
register_handlers(sio)
register_worker_namespace(sio)  # 本机 TTS worker 的反向长连接（/worker）


@fastapi_app.get("/health")
async def health():
    return {"status": "ok"}


# 必须早于下方的 SPA catch-all 路由，否则 /api/* 会被静态托管吞掉
fastapi_app.include_router(rest_router)
fastapi_app.include_router(voice_router)


# 生产：FastAPI 同端口托管前端构建物（单端口，tailnet 内访问最省事）
static_dir = os.path.abspath(os.getenv("STATIC_DIR", "static"))
INDEX_FILE = os.path.join(static_dir, "index.html")


def _index_response() -> HTMLResponse:
    """入口 HTML 禁止任何层级缓存（改版后用户必须立刻拿到新脚本）。"""
    with open(INDEX_FILE, "r", encoding="utf-8") as fh:
        html = fh.read()
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# 不用 mount("/") + StaticFiles(html=True)：它对未知路径返回纯文本 "Not Found"，
# 会顶掉 SPA 路由。改成显式文件查找 + 兜底回 index.html，任何路径都能进应用。
@fastapi_app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    # 未知 API 仍应返回 JSON 404，不要被前端遮住
    if full_path == "api" or full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if full_path:
        target = os.path.abspath(os.path.join(static_dir, full_path))
        # 防目录穿越，且只回真实存在的文件
        if target.startswith(static_dir + os.sep) and os.path.isfile(target):
            if os.path.basename(target) in ("index.html", "sw.js", "manifest.webmanifest"):
                return FileResponse(
                    target,
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
                )
            return FileResponse(target)

    if not os.path.isfile(INDEX_FILE):
        return JSONResponse({"detail": "frontend build missing"}, status_code=500)
    return _index_response()

# 组合：Socket.IO 处理 /socket.io，其余交给 FastAPI（REST + 静态）
app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="/socket.io")
