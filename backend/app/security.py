"""账号安全基元：密码哈希、令牌、输入校验、登录限流。

刻意只用 Python 标准库（hashlib.scrypt / secrets / hmac），不引入第三方依赖：
  - scrypt 是内存硬 KDF，抗 GPU/ASIC 爆破；每个账号独立随机盐；校验用
    hmac.compare_digest 做常量时间比较。
  - 登录令牌本身只发给客户端，数据库仅存 sha256(token)，库泄露也不能直接冒充。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time

from .policies import ALLOWED_AVATARS

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32
_SALT_BYTES = 16

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
# 控制字符（含换行）一律拒绝，避免昵称伪造系统提示 / 污染 Markdown 导出
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---------- 密码 ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """解析自存的哈希串并常量时间比较；任何格式异常都返回 False，不抛异常。"""
    try:
        algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        n_i, r_i, p_i = int(n), int(r), int(p)
        # 防止数据库被篡改后诱导超大 n 造成 CPU/内存 DoS
        if not (1024 <= n_i <= 65536 and 1 <= r_i <= 32 and 1 <= p_i <= 16):
            return False
        expected = bytes.fromhex(dk_hex)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=n_i, r=r_i, p=p_i, dklen=len(expected),
        )
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError, AttributeError):
        return False


_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    """用户名不存在时也做一次等价开销的哈希，抹平「用户是否存在」的时序差异。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("this-user-does-not-exist")
    verify_password(password, _DUMMY_HASH)


# ---------- 令牌 ----------

def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token if token and len(token) <= 256 else None


# ---------- 输入校验（白名单，异常由路由统一转 400） ----------

def validate_username(value: object) -> str:
    v = (value if isinstance(value, str) else "").strip()
    if not _USERNAME_RE.fullmatch(v):
        raise ValueError("用户名需为 3–32 位字母、数字或下划线")
    return v


def validate_password(value: object) -> str:
    if not isinstance(value, str) or not (8 <= len(value) <= 128):
        raise ValueError("密码长度需为 8–128 位")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("密码需同时包含字母和数字")
    if _CONTROL_RE.search(value):
        raise ValueError("密码不能包含控制字符")
    return value


def validate_nickname(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("昵称格式不正确")
    v = value.strip()
    if not (1 <= len(v) <= 32) or _CONTROL_RE.search(v):
        raise ValueError("昵称需为 1–32 个字符且不含控制字符")
    return v


def validate_avatar(value: object) -> str:
    v = value if isinstance(value, str) else ""
    return v if v in ALLOWED_AVATARS else ALLOWED_AVATARS[0]


def validate_text(value: object, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValueError("内容格式不正确")
    v = value.strip()
    if not v:
        raise ValueError("内容不能为空")
    if len(v) > max_len:
        raise ValueError(f"内容不能超过 {max_len} 个字符")
    if _CONTROL_RE.search(v.replace("\n", "").replace("\t", "")):
        raise ValueError("内容不能包含控制字符")
    return v


# ---------- 登录限流（单进程滑动窗口；多进程部署时换 Redis 即可） ----------

class LoginLimiter:
    def __init__(self, max_fail: int = 5, window_sec: int = 60):
        self.max_fail = max_fail
        self.window = window_sec
        self._fails: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(username: str, client_ip: str) -> str:
        return f"{username.lower()}|{client_ip}"

    async def check(self, key: str) -> None:
        async with self._lock:
            now = time.monotonic()
            hits = [t for t in self._fails.get(key, []) if now - t < self.window]
            if hits:
                self._fails[key] = hits
            else:
                self._fails.pop(key, None)
            if len(hits) >= self.max_fail:
                raise PermissionError("登录失败次数过多，请稍后再试")

    async def record_fail(self, key: str) -> None:
        async with self._lock:
            now = time.monotonic()
            hits = [t for t in self._fails.get(key, []) if now - t < self.window]
            hits.append(now)
            self._fails[key] = hits
            # 顺手清理长期不用的 key，避免内存无限增长
            if len(self._fails) > 5000:
                self._fails = {k: v for k, v in self._fails.items() if v and now - v[-1] < self.window}

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._fails.pop(key, None)
