#!/usr/bin/env python
"""DSH-CHAT 端到端冒烟测试（可重复运行）。

用法：
    python backend/tests/e2e_smoke.py

特点：
  - 自启动一个**独立端口 + 临时 SQLite** 的 uvicorn 实例，测完自动关闭并清理；
  - 绝不读写正式房间 data/dsh_chat.db（前后会校验正式库指纹未变）；
  - 覆盖账号/鉴权/注入防护、Socket.IO 实时、角色权限矩阵、AI 参数、
    撤回、搜索导出、踢出/恢复、改密/登出、数据库存储安全与前端构建物。

依赖：requests、python-socketio（客户端 polling 传输还需要 requests）。
退出码：0 = 全部通过，1 = 有失败项。
"""
from __future__ import annotations

import hashlib
import re
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import requests
import socketio

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
PROD_DB = ROOT / "data" / "dsh_chat.db"

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def cleanup_redis_prefix(prefix: str) -> None:
    """删掉隔离实例写下的调度状态，避免给正式 Redis 留测试残留。"""
    url = ""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("REDIS_URL="):
                url = line.split("=", 1)[1].strip()
                break
    except OSError:
        return
    if not url:
        return
    try:
        import redis as sync_redis
        cli = sync_redis.Redis.from_url(url, decode_responses=True, protocol=2, socket_timeout=2)
        keys = cli.keys(prefix + "*")
        if keys:
            cli.delete(*keys)
        cli.close()
    except Exception:
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fingerprint(path: Path) -> tuple:
    if not path.exists():
        return ("missing",)
    stat = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (stat.st_size, stat.st_mtime_ns, digest)


class SioClient:
    """收集指定事件的轻量 Socket.IO 客户端。"""

    def __init__(self, base: str, token: str | None = None):
        self.sio = socketio.Client()
        self.log: list[tuple[str, object]] = []
        self.connected = False
        events = (
            "joined", "history", "message:new", "presence", "auth:invalid",
            "compose:error", "message:recalled", "members:changed",
            "system:notice", "announcement",
        )
        for ev in events:
            self.sio.on(ev, lambda data, _ev=ev: self.log.append((_ev, data)))
        self.sio.on("connect", lambda: setattr(self, "connected", True))
        self.sio.connect(base, transports=["polling"])
        self.token = token
        if token is not None:
            self.sio.emit("join", {"token": token})
            time.sleep(0.35)

    def got(self, event: str) -> list:
        return [d for e, d in self.log if e == event]

    def clear(self) -> None:
        self.log.clear()

    def close(self) -> None:
        try:
            self.sio.disconnect()
        except Exception:
            pass


def main() -> int:
    prod_before = fingerprint(PROD_DB)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp_dir = tempfile.mkdtemp(prefix="dsh_e2e_")
    db_path = Path(tmp_dir) / "test.db"
    log_path = Path(tmp_dir) / "uvicorn.log"

    env = os.environ.copy()
    env["SQLITE_PATH"] = str(db_path)
    env["STATIC_DIR"] = str(FRONTEND_DIST)
    env["PYTHONPATH"] = str(BACKEND)
    # D5–D7：回归实例用独立 Redis 前缀 + 强制沉默的 Mock，
    # 既不写正式调度状态，也不会往测试库里插入 AI 消息影响既有断言。
    redis_prefix = f"dsh:e2e_smoke:{uuid.uuid4().hex[:8]}"
    env["DSH_REDIS_PREFIX"] = redis_prefix
    # 强制 Mock：即使 .env 里已填真实 DEEPSEEK_API_KEY，回归实例也绝不走真实 API。
    env["DSH_ADAPTER"] = "mock"
    env["DSH_MOCK_MODE"] = "always_silent"
    env["DSH_MOCK_DELAY_MS"] = "0"

    print(f"== 启动隔离实例 {base}（临时库 {db_path.name}）==")
    with log_path.open("w", encoding="utf-8") as logfile:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=str(BACKEND), env=env, stdout=logfile, stderr=subprocess.STDOUT,
        )
        try:
            ready = False
            for _ in range(60):
                if proc.poll() is not None:
                    break
                try:
                    if requests.get(base + "/health", timeout=1).status_code == 200:
                        ready = True
                        break
                except requests.RequestException:
                    time.sleep(0.25)
            if not ready:
                print("服务未能启动，日志：\n" + log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
                return 1

            run_checks(base, db_path)
            check("正式库全程未被写入（零污染）", fingerprint(PROD_DB) == prod_before,
                  f"{prod_before} -> {fingerprint(PROD_DB)}")

            print("\n== 结果 ==")
            print(f"PASS {PASSED} / {PASSED + FAILED}   FAIL {FAILED}")
            if FAILURES:
                print("失败项：" + "、".join(FAILURES))
            return 1 if FAILED else 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=6)
            # 父进程也持有日志文件句柄；Windows 下不先 close 就无法删除临时目录。
            logfile.close()
            time.sleep(0.3)
            for p in (db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                log_path.unlink(missing_ok=True)
                Path(tmp_dir).rmdir()
            except OSError:
                pass
            cleanup_redis_prefix(redis_prefix)


def run_checks(base: str, db_path: Path) -> None:
    def get(path: str, token: str | None = None, **kw):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.get(base + path, headers=h, timeout=10, **kw)

    def post(path: str, payload=None, token: str | None = None):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.post(base + path, json=payload or {}, headers=h, timeout=10)

    def put(path: str, payload=None, token: str | None = None):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.put(base + path, json=payload or {}, headers=h, timeout=10)

    def patch(path: str, payload=None, token: str | None = None):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.patch(base + path, json=payload or {}, headers=h, timeout=10)

    def delete(path: str, token: str | None = None):
        h = {"Authorization": f"Bearer {token}"} if token else {}
        return requests.delete(base + path, headers=h, timeout=10)

    clients: list[SioClient] = []

    try:
        # ---------- 1. 空白状态与鉴权边界 ----------
        print("\n== 1. 空白状态 / 鉴权边界 ==")
        check("health 200", get("/health").status_code == 200)
        st = get("/api/auth/status").json()
        check("隔离实例初始为空房间", st["user_count"] == 0 and not st["has_owner"], st)
        for path in ("/api/members", "/api/search?q=x", "/api/export?format=json", "/api/auth/me", "/api/ai/params", "/api/ai/status"):
            check(f"未登录 {path} -> 401", get(path).status_code == 401, get(path).status_code)
        check("Bearer 格式错误 -> 401",
              requests.get(base + "/api/auth/me", headers={"Authorization": "Token abc"}, timeout=10).status_code == 401)

        # ---------- 2. 注册校验 / 注入防护 ----------
        print("\n== 2. 注册校验 / SQL 注入防护 ==")
        bad_users = ["' OR 1=1--", "admin; DROP TABLE users", "a", "有中文", "x y", "u" * 40]
        for u in bad_users:
            r = post("/api/auth/register", {"username": u, "password": "Passw0rd!", "nickname": "坏人"})
            check(f"注入/非法用户名拒绝: {u[:20]}", r.status_code == 400, r.status_code)
        for pw in ("1234", "password", "abcdefgh", "12345678", "P" * 200):
            r = post("/api/auth/register", {"username": "weakuser", "password": pw, "nickname": "弱口令"})
            check(f"弱口令拒绝: {pw[:12]}", r.status_code == 400, r.status_code)
        check("注入后 users 表完好", get("/api/auth/status").json()["user_count"] == 0)

        # ---------- 3. 首个账号 = 群主 ----------
        print("\n== 3. 账号注册与首个群主 ==")
        def register(name: str, nick: str, avatar: str = "🐱"):
            r = post("/api/auth/register", {
                "username": name, "password": "Passw0rd!", "nickname": nick, "avatar": avatar,
            })
            return r

        r_owner = register("owner1", "群主")
        check("群主注册 200", r_owner.status_code == 200, r_owner.text[:120])
        owner = r_owner.json()["token"]
        owner_id = r_owner.json()["user"]["id"]
        check("首个账号自动为 owner", r_owner.json()["user"]["role"] == "owner")
        check("注册响应不含密码哈希", "password_hash" not in r_owner.json()["user"])

        r_admin = register("admin1", "管理员", "🦊")
        r_member = register("member1", "<img src=x>成员", "🐶")
        r_member2 = register("member2", "成员2", "🐼")
        admin = r_admin.json()["token"]
        admin_id = r_admin.json()["user"]["id"]
        member = r_member.json()["token"]
        member_id = r_member.json()["user"]["id"]
        member2 = r_member2.json()["token"]
        member2_id = r_member2.json()["user"]["id"]
        check("其余账号均为 member", all(x.json()["user"]["role"] == "member" for x in (r_admin, r_member, r_member2)))
        check("重复用户名 -> 409", register("owner1", "冒充者").status_code == 409)
        check("错误密码 -> 401", post("/api/auth/login", {"username": "owner1", "password": "Wrong1234"}).status_code == 401)
        check("不存在的用户 -> 401", post("/api/auth/login", {"username": "ghost99", "password": "Wrong1234"}).status_code == 401)
        check("篡改令牌 -> 401", get("/api/auth/me", owner + "x").status_code == 401)
        me = get("/api/auth/me", owner)
        check("me 返回本人且无敏感字段", me.status_code == 200 and "password_hash" not in me.json()["user"])

        # 撞库限流（独立用户名，不污染其他账号）
        codes = [post("/api/auth/login", {"username": "brute1", "password": "Bad12345"}).status_code for _ in range(6)]
        check("连续失败触发 429", codes[-1] == 429 and codes[:5] == [401] * 5, codes)

        # ---------- 4. Socket.IO 实时 + 身份不可伪造 ----------
        print("\n== 4. Socket.IO 实时链路 / 身份不可伪造 ==")
        bad = SioClient(base, "this-is-not-a-token")
        clients.append(bad)
        check("无效令牌 join 收到 auth:invalid", len(bad.got("auth:invalid")) >= 1, bad.log)
        bad.close()

        co = SioClient(base, owner); clients.append(co)
        ca = SioClient(base, admin); clients.append(ca)
        cm = SioClient(base, member); clients.append(cm)
        c2 = SioClient(base, member2); clients.append(c2)
        check("四客户端 join 均收到 joined", all(len(c.got("joined")) >= 1 for c in (co, ca, cm, c2)))
        check("joined 携带服务端身份", co.got("joined")[0]["user"]["username"] == "owner1")
        check("presence 在线数 = 4", any(d.get("online_count") == 4 for d in co.got("presence")),
              co.got("presence"))

        for c in (co, ca, cm, c2):
            c.clear()
        co.sio.emit("message", {"text": "群主说：开个测试"})
        time.sleep(0.35)
        ca.sio.emit("message", {"text": "管理员报到"})
        time.sleep(0.35)
        # 客户端自报身份字段必须被服务端忽略
        cm.sio.emit("message", {
            "text": "伪造身份测试", "nickname": "群主", "avatar": "🦄",
            "sender_id": "999999", "sender_type": "ai",
        })
        time.sleep(0.35)
        c2.sio.emit("message", {"text": "成员2 来了"})
        time.sleep(0.5)
        for c in (co, ca, cm, c2):
            check(f"客户端收到 4 条广播", len(c.got("message:new")) == 4, len(c.got("message:new")))
        forged = [d for d in co.got("message:new") if d["text"] == "伪造身份测试"][0]
        check("伪造 nickname 被忽略", forged["nickname"] == "<img src=x>成员", forged)
        check("伪造 avatar 被忽略", forged["avatar"] == "🐶", forged)
        check("伪造 sender_id 被忽略", forged["sender_id"] == str(member_id), forged)
        check("伪造 sender_type 被忽略", forged["sender_type"] == "human", forged)

        late = SioClient(base, owner); clients.append(late)
        check("晚加入者回填 4 条历史", len(late.got("history")[0]["messages"]) == 4, late.got("history"))
        late.close()

        # ---------- 5. 角色任命与权限矩阵 ----------
        print("\n== 5. 角色任命与权限矩阵 ==")
        def role_payload(uid, role): return {"user_id": uid, "role": role}
        def mute_payload(uid, minutes): return {"user_id": uid, "minutes": minutes}
        def kick_payload(uid): return {"user_id": uid}

        check("成员任命管理员 -> 403", post("/api/admin/role", role_payload(member2_id, "admin"), member).status_code == 403)
        check("成员改 AI 参数 -> 403", put("/api/ai/params", {"cooldown_ms": 90000}, member).status_code == 403)
        check("成员改群公告 -> 403", put("/api/group/announcement", {"text": "越权公告"}, member).status_code == 403)
        check("成员踢人 -> 403", post("/api/admin/kick", kick_payload(member2_id), member).status_code == 403)
        check("成员读 AI 参数 editable=false", get("/api/ai/params", member).json()["editable"] is False)
        check("群主任命管理员 -> 200", post("/api/admin/role", role_payload(admin_id, "admin"), owner).status_code == 200)
        check("成员表反映管理员角色",
              next(u for u in get("/api/members", owner).json()["members"] if u["id"] == admin_id)["role"] == "admin")
        check("管理员任命他人为管理员 -> 403",
              post("/api/admin/role", role_payload(member2_id, "admin"), admin).status_code == 403)
        check("管理员改 AI 参数 -> 403", put("/api/ai/params", {"cooldown_ms": 90000}, admin).status_code == 403)
        check("管理员踢群主 -> 403", post("/api/admin/kick", kick_payload(owner_id), admin).status_code == 403)
        check("管理员改群公告 -> 200", put("/api/group/announcement", {"text": "今晚 21:00 开测"}, admin).status_code == 200)
        time.sleep(0.4)  # polling 传输：留一拍让广播到达客户端
        check("公告广播送达", len(ca.got("announcement")) >= 1, ca.got("announcement"))

        # 禁言：服务端每次回查账号状态
        check("管理员禁言成员 -> 200", post("/api/admin/mute", mute_payload(member_id, 10), admin).status_code == 200)
        cm.clear()
        cm.sio.emit("message", {"text": "禁言期间不该发出"})
        time.sleep(0.4)
        check("禁言成员发言被拒 compose:error", len(cm.got("compose:error")) >= 1, cm.log)
        check("禁言消息未落库", all(m["text"] != "禁言期间不该发出" for m in get("/api/export?format=json", owner).json()["messages"]))
        check("解除禁言 -> 200", post("/api/admin/mute", mute_payload(member_id, 0), admin).status_code == 200)
        cm.clear()
        cm.sio.emit("message", {"text": "解禁后恢复发言"})
        time.sleep(0.4)
        check("解禁后可发言", any(d["text"] == "解禁后恢复发言" for d in cm.got("message:new")))

        # ---------- 6. AI 参数（仅群主） ----------
        print("\n== 6. AI 参数白名单与范围 ==")
        check("群主改 AI 参数 -> 200", put("/api/ai/params", {"cooldown_ms": 90000, "ai_enabled": False}, owner).status_code == 200)
        p = get("/api/ai/params", owner).json()["params"]
        check("参数已持久化", p["cooldown_ms"] == "90000" and p["ai_enabled"] == "0", p)
        check("越界值 -> 400", put("/api/ai/params", {"cooldown_ms": 99999999}, owner).status_code == 400)
        check("未知 key -> 400", put("/api/ai/params", {"injected_key": "x"}, owner).status_code == 400)
        check("非法类型 -> 400", put("/api/ai/params", {"max_tokens": "120 OR 1=1"}, owner).status_code == 400)

        # ---------- 7. 撤回分级 ----------
        print("\n== 7. 撤回权限（微信/QQ 风格） ==")
        c2.sio.emit("message", {"text": "供管理员撤回的普通成员消息"})
        time.sleep(0.4)
        msgs = get("/api/export?format=json", owner).json()["messages"]
        own_msg = next(m["id"] for m in msgs if m["text"] == "伪造身份测试")
        admin_msg = next(m["id"] for m in msgs if m["sender_id"] == str(admin_id))
        owner_msg = next(m["id"] for m in msgs if m["sender_id"] == str(owner_id))
        plain_msg = next(m["id"] for m in msgs if m["text"] == "供管理员撤回的普通成员消息")
        check("成员撤回自己 2 分钟内消息 -> 200", delete(f"/api/messages/{own_msg}", member).status_code == 200)
        check("成员撤回他人消息 -> 403", delete(f"/api/messages/{admin_msg}", member).status_code == 403)
        check("管理员撤回普通成员消息 -> 200", delete(f"/api/messages/{plain_msg}", admin).status_code == 200)
        check("管理员撤回群主消息 -> 403", delete(f"/api/messages/{owner_msg}", admin).status_code == 403)
        check("群主撤回任意消息 -> 200", delete(f"/api/messages/{owner_msg}", owner).status_code == 200)
        check("撤回后导出不再包含", all(m["id"] != own_msg for m in get("/api/export?format=json", owner).json()["messages"]))
        check("撤回广播送达", any(len(c.got("message:recalled")) >= 1 for c in (co, ca, cm, c2)))

        # ---------- 8. 搜索 / 导出 ----------
        print("\n== 8. 搜索与导出 ==")
        cm.sio.emit("message", {"text": "100%_discount 关键词"})
        time.sleep(0.4)
        hit = get("/api/search?q=100%25", owner).json()
        check("LIKE 通配符被转义（搜 % 不全表命中）", len(hit["messages"]) == 1, [m["text"] for m in hit["messages"]])
        zh = get("/api/search", owner, params={"q": "关键词"}).json()
        check("搜索中文命中", any("关键词" in m["text"] for m in zh["messages"]), zh["messages"])
        check("搜索 limit 非法 -> 422", get("/api/search?q=x&limit=999", owner).status_code == 422)
        check("导出 format 非法 -> 400", get("/api/export?format=exe", owner).status_code == 400)
        export = get("/api/export?format=json", owner)
        check("导出 JSON 200 且含元信息", export.status_code == 200 and "message_count" in export.json())
        check("导出响应 no-store", "no-store" in export.headers.get("Cache-Control", ""))
        check("导出为附件下载", "attachment" in export.headers.get("Content-Disposition", ""))
        md = get("/api/export?format=md", owner).text
        check("Markdown 对 HTML 昵称转义", "\\<img" in md, [l for l in md.splitlines() if "img" in l][:2])
        check("未登录导出 -> 401", get("/api/export?format=json").status_code == 401)

        # ---------- 9. 踢出 / 恢复 / 改密 / 登出 ----------
        print("\n== 9. 踢出、恢复、改密与登出 ==")
        check("管理员踢普通成员 -> 200", post("/api/admin/kick", kick_payload(member2_id), admin).status_code == 200)
        check("被踢账号令牌立即失效", get("/api/auth/me", member2).status_code == 401)
        time.sleep(0.3)
        check("被踢连接收到 auth:invalid", len(c2.got("auth:invalid")) >= 1, c2.log)
        check("被踢账号无法登录 -> 403", post("/api/auth/login", {"username": "member2", "password": "Passw0rd!"}).status_code == 403)
        check("管理员恢复成员 -> 403", post("/api/admin/restore", kick_payload(member2_id), admin).status_code == 403)
        check("群主恢复成员 -> 200", post("/api/admin/restore", kick_payload(member2_id), owner).status_code == 200)
        check("恢复后可重新登录", post("/api/auth/login", {"username": "member2", "password": "Passw0rd!"}).status_code == 200)

        pw = post("/api/auth/password", {"old_password": "Passw0rd!", "new_password": "NewPassw0rd!"}, admin)
        check("改密 200 且返回新令牌", pw.status_code == 200 and "token" in pw.json(), pw.text[:120])
        check("改密后旧令牌失效", get("/api/auth/me", admin).status_code == 401)
        new_admin = pw.json()["token"]
        check("改密后新令牌可用", get("/api/auth/me", new_admin).status_code == 200)
        check("旧密码无法登录", post("/api/auth/login", {"username": "admin1", "password": "Passw0rd!"}).status_code == 401)
        check("新密码可以登录", post("/api/auth/login", {"username": "admin1", "password": "NewPassw0rd!"}).status_code == 200)

        check("改自己资料 -> 200", patch("/api/me", {"nickname": "成员新昵称", "avatar": "🐰"}, member).status_code == 200)
        check("登出 -> 200", post("/api/auth/logout", {}, member).status_code == 200)
        check("登出后令牌失效", get("/api/auth/me", member).status_code == 401)

        # ---------- 10. 数据库存储安全 ----------
        print("\n== 10. 数据库存储安全 ==")
        conn = sqlite3.connect(str(db_path))
        hashes = [r[0] for r in conn.execute("SELECT password_hash FROM users")]
        check("密码均为 scrypt 哈希", all(h.startswith("scrypt$") for h in hashes) and len(hashes) == 4)
        check("库中无明文密码", all("Passw0rd!" not in h and "NewPassw0rd!" not in h for h in hashes))
        ths = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
        check("会话只存 sha256(token)", all(len(t) == 64 and all(c in "0123456789abcdef" for c in t) for t in ths))
        check("消息 sender_id 为账号 ID", all(
            r[0] in {str(owner_id), str(admin_id), str(member_id), str(member2_id)}
            for r in conn.execute("SELECT sender_id FROM messages")
        ))
        check("SQL 注入后核心表仍在", {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >=
              {"users", "sessions", "messages", "ai_params", "group_meta"})
        check("integrity_check = ok", conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
        conn.close()

        # ---------- 11. 前端构建物 ----------
        print("\n== 11. 前端构建物 ==")
        index = get("/").text
        check("首页由同端口托管", "<title>" in index and "/assets/" in index)
        check("PWA manifest 200", get("/manifest.webmanifest").status_code == 200)
        check("未注册 Service Worker（避免缓存中毒）", "self.addEventListener" not in get("/sw.js").text)
        js_files = [l for l in index.split('"') if l.startswith("/assets/index-") and l.endswith(".js")]
        bundle = get(js_files[0]).text if js_files else ""
        check("构建物含登录注册 UI", "第一个注册的账号将成为群主" in bundle, len(bundle))
        # 注意：React DOM 运行时自身的属性白名单里就含 "dangerouslySetInnerHTML" 字符串，
        # 因此对打包产物做子串判断必然误报，改为检查业务源码（去掉注释后）没有实际使用。
        sources = list((ROOT / "frontend" / "src").rglob("*.ts")) + list((ROOT / "frontend" / "src").rglob("*.tsx"))
        code = "\n".join(p.read_text(encoding="utf-8") for p in sources)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        check("业务源码不使用 dangerouslySetInnerHTML / innerHTML / eval",
              not any(k in code for k in ("dangerouslySetInnerHTML", "innerHTML", "eval(")), len(sources))
    finally:
        for c in clients:
            c.close()


if __name__ == "__main__":
    sys.exit(main())
