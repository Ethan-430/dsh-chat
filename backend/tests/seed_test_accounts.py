#!/usr/bin/env python
"""在**正式房间**造 / 清测试账号，方便手动验证群主-管理员-成员权限。

用法：
    python backend/tests/seed_test_accounts.py            # 造号（幂等，可重复运行）
    python backend/tests/seed_test_accounts.py --clean    # 清理这些账号及其消息/会话

约定：
  - 只操作 ACCOUNTS 白名单里的用户名，绝不触碰群主或其他账号；
  - 每次写库前自动备份 data/dsh_chat.db（带时间戳）；
  - 注册走正常 HTTP 流程（scrypt 哈希 / 会话 / 成员广播），只有「任命管理员」
    这一步直接改库——因为脚本不应持有群主的登录令牌；
  - 账号密码是公开的本地测试口令，切勿用于真实部署。

服务地址可用 DSH_BASE 覆盖，数据库可用 SQLITE_PATH 覆盖。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("SQLITE_PATH", str(ROOT / "data" / "dsh_chat.db")))
BASE = os.environ.get("DSH_BASE", "http://127.0.0.1:8000").rstrip("/")

PASSWORD = "Test1234!"
# (用户名, 昵称, 头像, 目标角色) —— 群主由用户自己持有，这里不创建 owner
ACCOUNTS = [
    ("test_admin", "测试·管理员", "🦊", "admin"),
    ("test_member1", "测试成员A", "🐶", "member"),
    ("test_member2", "测试成员B", "🐼", "member"),
]
USERNAMES = tuple(a[0] for a in ACCOUNTS)


def backup() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = DB_PATH.with_name(f"{DB_PATH.name}.bak-{ts}-seed")
    shutil.copy2(DB_PATH, dst)
    return dst


def rows(sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def exec_sql(sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def clean() -> int:
    if not DB_PATH.exists():
        print("数据库不存在，无需清理")
        return 0
    bak = backup()
    print(f"已备份：{bak.name}")
    placeholders = ",".join("?" for _ in USERNAMES)
    found = rows(f"SELECT id, username, role FROM users WHERE username IN ({placeholders})", USERNAMES)
    if not found:
        print("没有找到测试账号，正式库未改动")
        return 0
    ids = tuple(r[0] for r in found)
    id_ph = ",".join("?" for _ in ids)
    n_msg = exec_sql(
        f"DELETE FROM messages WHERE sender_id IN ({','.join('?' for _ in ids)})",
        tuple(str(i) for i in ids),
    )
    n_sess = exec_sql(f"DELETE FROM sessions WHERE user_id IN ({id_ph})", ids)
    n_user = exec_sql(f"DELETE FROM users WHERE id IN ({id_ph})", ids)
    print(f"已删除：账号 {n_user} 个 / 会话 {n_sess} 个 / 这些账号发的消息 {n_msg} 条")
    for r in rows("SELECT id, username, role, status FROM users ORDER BY id"):
        print("  保留：", r)
    print("群主账号与原有消息均未改动")
    return 0


def create() -> int:
    try:
        health = requests.get(BASE + "/health", timeout=5)
        health.raise_for_status()
        status = requests.get(BASE + "/api/auth/status", timeout=5).json()
    except requests.RequestException as exc:
        print(f"服务不可用（{BASE}）：{exc}\n请先 bash run-local.sh 启动服务")
        return 1

    # 首个注册账号会自动成为群主。空白房间里必须先由真人注册群主，
    # 否则 test_admin 会误占 owner，随后又被改成 admin，导致群里没有群主。
    if not status.get("has_owner"):
        print("房间尚无群主：请先用浏览器注册第一个账号（自动成为群主），再运行本脚本造测试账号。")
        print("（否则首个测试账号会误占群主角色，权限体系将没有 owner）")
        return 1

    bak = backup()
    print(f"已备份正式库：{bak.name}")
    tokens: dict[str, str] = {}

    # 1) 走正常注册流程；已存在则登录（幂等）
    for username, nickname, avatar, _role in ACCOUNTS:
        exists = rows("SELECT 1 FROM users WHERE username=?", (username,))
        if exists:
            r = requests.post(BASE + "/api/auth/login",
                              json={"username": username, "password": PASSWORD}, timeout=10)
            if r.status_code != 200:
                print(f"  {username} 已存在但用测试口令登录失败（{r.status_code}），跳过")
                continue
            print(f"  {username} 已存在，直接复用")
        else:
            r = requests.post(BASE + "/api/auth/register", json={
                "username": username, "password": PASSWORD,
                "nickname": nickname, "avatar": avatar,
            }, timeout=10)
            if r.status_code != 200:
                print(f"  {username} 注册失败：{r.status_code} {r.text[:160]}")
                continue
            print(f"  {username} 注册成功（初始 role={r.json()['user']['role']}）")
        tokens[username] = r.json()["token"]

    # 2) 任命管理员：白名单内且目标角色为 admin 的账号，直接改库（不借用群主令牌）
    for username, _nick, _avatar, role in ACCOUNTS:
        if role == "admin":
            n = exec_sql(
                "UPDATE users SET role='admin' WHERE username=? AND role<>'owner'",
                (username,),
            )
            print(f"  {username} -> admin（影响 {n} 行）")

    # 3) 触发成员变更广播，让已打开的群主窗口自动刷新成员面板
    admin_token = tokens.get("test_admin")
    if admin_token:
        r = requests.patch(BASE + "/api/me",
                           json={"nickname": "测试·管理员", "avatar": "🦊"},
                           headers=auth_headers(admin_token), timeout=10)
        print(f"  成员变更广播：{'已触发' if r.status_code == 200 else r.status_code}")

    # 4) 清理造号过程产生的临时会话（用户自己登录时会重新签发）
    for username, token in tokens.items():
        requests.post(BASE + "/api/auth/logout", json={},
                      headers=auth_headers(token), timeout=10)
    print(f"  已登出 {len(tokens)} 个造号会话")

    # 5) 只读复核
    print("\n== 复核 ==")
    status = requests.get(BASE + "/api/auth/status", timeout=10).json()
    print("  /api/auth/status:", status)
    login = requests.post(BASE + "/api/auth/login",
                          json={"username": "test_admin", "password": PASSWORD}, timeout=10)
    if login.status_code == 200:
        h = auth_headers(login.json()["token"])
        members = requests.get(BASE + "/api/members", headers=h, timeout=10).json()
        for m in members["members"]:
            print(f"  id={m['id']}  {m['username']:<14} role={m['role']:<7} status={m['status']}")
        ai = requests.get(BASE + "/api/ai/params", headers=h, timeout=10).json()
        print("  管理员可见 AI 参数 editable =", ai["editable"], "（应为 False）")
        denied = requests.put(BASE + "/api/ai/params", json={"cooldown_ms": 90000},
                              headers=h, timeout=10)
        print("  管理员改 AI 参数 ->", denied.status_code, "（应为 403）")
        requests.post(BASE + "/api/auth/logout", json={}, headers=h, timeout=10)

    print("\n== 测试账号（密码统一）==")
    print(f"  {'用户名':<16}{'角色':<8}{'昵称':<12}密码")
    for username, nickname, _avatar, role in ACCOUNTS:
        print(f"  {username:<16}{role:<8}{nickname:<12}{PASSWORD}")
    print("\n清理命令：python backend/tests/seed_test_accounts.py --clean")
    return 0


if __name__ == "__main__":
    sys.exit(clean() if "--clean" in sys.argv[1:] else create())
