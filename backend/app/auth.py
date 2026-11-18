"""FastAPI 鉴权依赖：优先 X-DSH-Token，其次 Authorization: Bearer <token>。

为什么需要 X-DSH-Token：
    发布到分享域名后，前置网关会把请求里的 Authorization 头**替换**成它自己的 JWT
    （实测到达应用时已不是用户令牌），导致所有需要登录的接口一律 401。
    自定义头 X-DSH-Token 会原样透传，因此作为首选；Authorization 仍然兼容，
    便于本机直连、tailnet 部署和既有测试保持不变。

令牌不放 Cookie（避免 CSRF）；每个受保护路由都通过依赖实时回查数据库，
因此改密 / 踢出 / 会话过期能立即生效，不依赖进程内缓存。
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from . import db
from .security import parse_bearer

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def _pick_token(x_dsh_token: str | None, authorization: str | None) -> str | None:
    # 1) 自定义头优先：网关会改写 Authorization，但不会动这个头
    if x_dsh_token:
        raw = x_dsh_token.strip()
        if raw:
            # 允许带或不带 "Bearer " 前缀
            token = parse_bearer(raw) or (raw if len(raw) <= 256 else None)
            if token:
                return token
    # 2) 兼容标准写法
    return parse_bearer(authorization)


async def _resolve(x_dsh_token: str | None, authorization: str | None) -> tuple[dict, str]:
    token = _pick_token(x_dsh_token, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录", headers=_UNAUTHORIZED_HEADERS)
    user = await db.resolve_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录", headers=_UNAUTHORIZED_HEADERS)
    return user, token


async def current_user(
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
) -> dict:
    user, _ = await _resolve(x_dsh_token, authorization)
    return user


async def current_session(
    x_dsh_token: str | None = Header(default=None, alias="X-DSH-Token"),
    authorization: str | None = Header(default=None),
) -> tuple[dict, str]:
    return await _resolve(x_dsh_token, authorization)
