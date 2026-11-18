#!/usr/bin/env python
"""D5–D7 专项端到端测试（DSH 适配层 + Mock harness + Redis 调度器 + 逐字流式）。

用法：先确保本机 Redis 已启动（bash redis-local.sh run），然后
    python backend/tests/e2e_d5.py

特点：
  - 起两个隔离实例（随机端口 + 临时 SQLite + 独立 Redis 前缀）：
      A: DSH_MOCK_MODE=always_speak  验证触发 / 冷却 / 流式 / 落库
      B: DSH_MOCK_MODE=always_silent 验证「已读不回」不落库、不广播、不动冷却
  - 全程不写正式房间 data/dsh_chat.db，前后校验文件指纹；
  - 测试结束后删除自己写下的 Redis 键，正式 prefix dsh:main:* 保持干净。

覆盖：@ 必叫、疑问句、被无视 N 条、45s 静默（用 5s 参数加速）、60s 冷却、
连续主动上限、@ 清零、每日调用上限、ai_enabled 开关、参数热更、dsh:typing/delta/done/silent
事件契约、AI 消息进入 history / 搜索 / 导出、should_speak=false 语义。
"""
from __future__ import annotations

import hashlib
import os
import socket
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fingerprint(path: Path) -> tuple:
    if not path.exists():
        return ("missing",)
    st = path.stat()
    return (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())


def redis_url() -> str:
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("REDIS_URL="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def sync_redis():
    import redis
    url = redis_url()
    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True, protocol=2, socket_timeout=3)


def delete_prefix(cli, prefix: str) -> None:
    if cli is None:
        return
    try:
        keys = cli.keys(prefix + "*")
        if keys:
            cli.delete(*keys)
    except Exception:
        pass


def wait_until(pred, timeout: float = 5.0, interval: float = 0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return False


class Instance:
    """独立 uvicorn 实例：随机端口 + 临时 SQLite + 独立 Redis 前缀。"""

    def __init__(self, mode: str):
        self.mode = mode
        self.prefix = f"dsh:e2e_d5_{mode}:{uuid.uuid4().hex[:8]}"
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.tmp: Path | None = None
        self.proc: subprocess.Popen | None = None
        self.logfile = None

    def start(self) -> bool:
        self.tmp = Path(tempfile.mkdtemp(prefix="dsh_d5_"))
        db_path = self.tmp / "test.db"
        log_path = self.tmp / "uvicorn.log"
        env = os.environ.copy()
        env.update({
            "SQLITE_PATH": str(db_path),
            "STATIC_DIR": str(FRONTEND_DIST),
            "PYTHONPATH": str(BACKEND),
            "DSH_REDIS_PREFIX": self.prefix,
            # 强制 Mock：即使 .env 里已填真实 DEEPSEEK_API_KEY，专项实例也绝不走真实 API。
            "DSH_ADAPTER": "mock",
            "DSH_MOCK_MODE": self.mode,
            "DSH_MOCK_DELAY_MS": "0",
        })
        self.logfile = log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(BACKEND), env=env, stdout=self.logfile, stderr=subprocess.STDOUT,
        )
        for _ in range(80):
            if self.proc.poll() is not None:
                break
            try:
                if requests.get(self.base + "/health", timeout=1).status_code == 200:
                    return True
            except requests.RequestException:
                time.sleep(0.2)
        print("实例启动失败，日志：\n" + log_path.read_text(encoding="utf-8", errors="replace")[-3000:])
        return False

    def stop(self, cli) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=6)
        if self.logfile:
            self.logfile.close()
        if FAILED and self.tmp:
            try:
                print(f"\n=== 服务端日志 {self.mode}（尾部） ===")
                print((self.tmp / "uvicorn.log").read_text(encoding="utf-8", errors="replace")[-4000:])
            except OSError:
                pass
        time.sleep(0.25)
        delete_prefix(cli, self.prefix)
        if self.tmp:
            for p in self.tmp.iterdir():
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                self.tmp.rmdir()
            except OSError:
                pass


class Client:
    def __init__(self, base: str, token: str):
        self.sio = socketio.Client()
        self.log: list[tuple[str, object]] = []
        events = (
            "joined", "history", "message:new", "presence",
            "dsh:typing", "dsh:delta", "dsh:done", "dsh:silent",
        )
        for ev in events:
            self.sio.on(ev, lambda data, _ev=ev: self.log.append((_ev, data)))
        self.sio.connect(base, transports=["polling"])
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


def register(base: str, name: str) -> str:
    r = requests.post(base + "/api/auth/register", json={
        "username": name, "password": "Passw0rd!", "nickname": name, "avatar": "🐱",
    }, timeout=10)
    return r.json()["token"]


def get(base: str, path: str, token: str):
    return requests.get(base + path, headers={"Authorization": f"Bearer {token}"}, timeout=10)


def put_params(base: str, token: str, **kw) -> bool:
    r = requests.put(base + "/api/ai/params", headers={"Authorization": f"Bearer {token}"},
                     json={"params": kw}, timeout=10)
    return r.status_code == 200


def status(base: str, token: str) -> dict:
    return get(base, "/api/ai/status", token).json()


def no_new_done(client: Client, seconds: float = 0.7) -> bool:
    time.sleep(seconds)
    return len(client.got("dsh:done")) == 0


# ---------------------------------------------------------------- 场景 A


def run_speak_checks(base: str, token: str) -> None:
    c = Client(base, token)
    try:
        print("\n== A. always_speak：触发 / 冷却 / 流式 / 落库 ==")
        check("调度器已就绪且适配器为 mock",
              status(base, token)["ready"] and status(base, token)["adapter"] == "mock",
              status(base, token))
        check("未登录 /api/ai/status -> 401",
              requests.get(base + "/api/ai/status", timeout=10).status_code == 401)

        # 关掉静默 / 被无视的干扰，缩短冷却以便快速验证各闸门
        check("群主批量设置 AI 参数 -> 200", put_params(
            base, token, ai_enabled=True, cooldown_ms=1000, silence_trigger_ms=600000,
            ignore_threshold=50, consecutive_limit=10, daily_call_limit=200,
            max_tokens=120, max_latency_ms=8000,
        ))
        st = status(base, token)
        check("状态接口反映热更新参数", st["ai_enabled"] is True and st["daily_call_limit"] == 200, st)

        # 1. @ 必叫：typing -> delta -> done
        c.clear()
        c.sio.emit("message", {"text": "@DSH 说两句"})
        check("@ 触发 DSH 发言", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-5:])
        typings = c.got("dsh:typing")
        check("广播 dsh:typing 开始与结束",
              any(d.get("active") is True for d in typings) and any(d.get("active") is False for d in typings),
              typings)
        check("dsh:typing 携带 trigger=mention",
              any(d.get("active") is True and d.get("trigger") == "mention" for d in typings), typings)
        deltas = c.got("dsh:delta")
        done = c.got("dsh:done")[0] if c.got("dsh:done") else {}
        check("逐字流式收到多个 delta", len(deltas) >= 3, len(deltas))
        check("delta 拼接等于最终正文",
              "".join(str(d.get("text", "")) for d in deltas) == done.get("text", None),
              (len(deltas), done.get("text", "")))
        check("AI 消息身份符合规格第八节",
              done.get("sender_type") == "ai" and done.get("sender_id") == "dsh"
              and done.get("nickname") == "DSH · AI" and done.get("avatar") == "🤖", done)
        check("AI 消息含 id / created_at",
              isinstance(done.get("id"), int) and isinstance(done.get("created_at"), int), done)
        exported = get(base, "/api/export?format=json", token).json()["messages"]
        check("AI 消息已落库（导出可见）", any(m.get("id") == done.get("id") for m in exported))
        st = status(base, token)
        check("发言计数与状态正确",
              st["calls_today"] == 1 and st["consecutive_init"] == 0
              and st["since_dsh_count"] == 0 and st["last_speak_ts"] > 0, st)

        # 2. 冷却闸：紧接着的疑问句不叫醒
        c.clear()
        c.sio.emit("message", {"text": "你觉得呢？"})
        check("60s 冷却闸拦住疑问句", no_new_done(c), c.got("dsh:done"))
        st = status(base, token)
        check("决策记录为 cooldown", "cooldown" in st["last_decision"], st["last_decision"])
        check("冷却拦截不消耗调用次数", st["calls_today"] == 1, st)

        # 3. @ 绕过冷却
        c.clear()
        c.sio.emit("message", {"text": "@DSH 再来一句"})
        check("@ 绕过冷却再次发言", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5),
              (c.sio.connected, c.log[-5:]))
        check("调用计数 +1", status(base, token)["calls_today"] == 2)
        check("被 @ 后连续主动计数清零", status(base, token)["consecutive_init"] == 0)

        # 4. 疑问句触发（等过 1s 冷却）
        time.sleep(1.15)
        c.clear()
        c.sio.emit("message", {"text": "这是为什么呢？"})
        check("疑问句触发发言", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-5:])
        st = status(base, token)
        check("决策记录为 spoke:question", st["last_decision"] == "spoke:question", st["last_decision"])
        check("连续主动计数 = 1", st["consecutive_init"] == 1, st)

        # 5. 话痨闸：连续主动上限 2
        time.sleep(1.15)
        c.clear()
        c.sio.emit("message", {"text": "谁有空聊聊？"})
        check("第二次主动发言成功", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-5:])
        check("连续主动计数 = 2", status(base, token)["consecutive_init"] == 2, status(base, token))
        check("热更 consecutive_limit=2", put_params(base, token, consecutive_limit=2))
        time.sleep(1.15)
        c.clear()
        c.sio.emit("message", {"text": "还有人吗？"})
        check("话痨闸拦住第三次主动发言", no_new_done(c), c.got("dsh:done"))
        st = status(base, token)
        check("决策记录为 consecutive", "consecutive" in st["last_decision"], st["last_decision"])

        # 6. @ 清零话痨计数
        c.clear()
        c.sio.emit("message", {"text": "@DSH 点名"})
        check("被 @ 可突破话痨闸", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-5:])
        check("被 @ 后计数清零", status(base, token)["consecutive_init"] == 0, status(base, token))

        # 7. 被无视阈值 ignored
        check("热更 ignore_threshold=3 / consecutive_limit=10",
              put_params(base, token, ignore_threshold=3, consecutive_limit=10))
        time.sleep(1.15)
        c.clear()
        for t in ("第一条闲聊", "第二条闲聊", "第三条闲聊"):
            c.sio.emit("message", {"text": t})
            time.sleep(0.12)
        check("连续 3 条无人理触发 ignored", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-6:])
        st = status(base, token)
        check("决策记录为 spoke:ignored", st["last_decision"] == "spoke:ignored", st["last_decision"])
        check("发言后计数归零", st["since_dsh_count"] == 0, st)

        # 8. 静默定时器（参数调成 5s 加速验收）
        check("热更 silence_trigger_ms=5000", put_params(base, token, silence_trigger_ms=5000, ignore_threshold=50))
        time.sleep(1.15)
        c.clear()
        c.sio.emit("message", {"text": "我先去忙"})
        check("静默 5s 后自动叫醒 DSH", wait_until(lambda: len(c.got("dsh:done")) >= 1, 8), c.log[-6:])
        st = status(base, token)
        check("决策记录为 spoke:silence", st["last_decision"] == "spoke:silence", st["last_decision"])

        # 9. ai_enabled=0 总开关
        check("关闭 ai_enabled", put_params(base, token, ai_enabled=False, silence_trigger_ms=600000))
        time.sleep(1.15)
        c.clear()
        c.sio.emit("message", {"text": "@DSH 你还醒着吗"})
        check("关闭后连 @ 也不叫醒", no_new_done(c), c.got("dsh:done"))
        st = status(base, token)
        check("决策记录为 disabled", "disabled" in st["last_decision"], st["last_decision"])
        check("关闭后状态 ai_enabled=false", st["ai_enabled"] is False, st)

        # 10. 每日调用硬上限
        cli = sync_redis()
        if cli:
            try:
                for k in cli.keys(f"{INST_A_PREFIX}:calls:*"):
                    cli.delete(k)
            except Exception:
                pass
        check("热更 daily_call_limit=1 / 重新开启",
              put_params(base, token, ai_enabled=True, daily_call_limit=1))
        c.clear()
        c.sio.emit("message", {"text": "@DSH 第一次"})
        check("上限内首次调用成功", wait_until(lambda: len(c.got("dsh:done")) >= 1, 5), c.log[-5:])
        c.clear()
        c.sio.emit("message", {"text": "@DSH 第二次"})
        check("触达每日上限后不再发言", no_new_done(c), c.got("dsh:done"))
        st = status(base, token)
        check("决策记录为 daily_limit", "daily_limit" in st["last_decision"], st["last_decision"])
        check("当日调用数锁定为 1", st["calls_today"] == 1, st)

        # 11. AI 消息进入 history / 搜索 / 导出
        put_params(base, token, daily_call_limit=200)
        late = Client(base, token)
        try:
            hist = late.got("history")[0]["messages"] if late.got("history") else []
            check("晚加入者 history 含 AI 消息", any(m.get("sender_type") == "ai" for m in hist), len(hist))
        finally:
            late.close()
        ai_msgs = [m for m in get(base, "/api/export?format=json", token).json()["messages"]
                   if m.get("sender_type") == "ai"]
        check("导出 JSON 含多条 AI 消息", len(ai_msgs) >= 5, len(ai_msgs))
        keyword = ai_msgs[0]["text"][:5]
        hit = requests.get(base + "/api/search", params={"q": keyword},
                           headers={"Authorization": f"Bearer {token}"}, timeout=10).json()
        check("AI 消息可被关键词搜索", any(m.get("sender_type") == "ai" for m in hit["messages"]), hit)
        check("业务源码无 innerHTML / eval", not any(
            k in (ROOT / "frontend" / "src" / "components" / "DshTyping.tsx").read_text(encoding="utf-8")
            for k in ("innerHTML", "eval(")
        ))
    finally:
        c.close()


# ---------------------------------------------------------------- 场景 B

INST_A_PREFIX = ""


def run_silent_checks(base: str, token: str) -> None:
    print("\n== B. always_silent：已读不回语义 ==")
    put_params(base, token, ai_enabled=True, cooldown_ms=1000,
               silence_trigger_ms=600000, ignore_threshold=50,
               consecutive_limit=2, daily_call_limit=200)
    c = Client(base, token)
    try:
        c.clear()
        c.sio.emit("message", {"text": "@DSH 说句话"})
        check("收到 dsh:silent（裁决不发言）",
              wait_until(lambda: len(c.got("dsh:silent")) >= 1, 5), c.log[-6:])
        time.sleep(0.4)
        check("未广播任何 dsh:done", len(c.got("dsh:done")) == 0, c.got("dsh:done"))
        check("未广播任何正文 delta", len(c.got("dsh:delta")) == 0, len(c.got("dsh:delta")))
        check("typing 有始有终",
              any(d.get("active") is True for d in c.got("dsh:typing"))
              and any(d.get("active") is False for d in c.got("dsh:typing")),
              c.got("dsh:typing"))
        msgs = get(base, "/api/export?format=json", token).json()["messages"]
        check("沉默不落库（只有 1 条人类消息）",
              len(msgs) == 1 and msgs[0]["sender_type"] == "human", msgs)
        st = status(base, token)
        check("沉默不动冷却与连续计数",
              st["last_speak_ts"] == 0 and st["consecutive_init"] == 0, st)
        check("沉默不计入「距上次发言」清零", st["since_dsh_count"] == 1, st)
        check("调用计数仍 +1（成本闸门照算）", st["calls_today"] == 1, st)
        check("决策记录为 silent:mention", st["last_decision"] == "silent:mention", st["last_decision"])
    finally:
        c.close()


def main() -> int:
    global INST_A_PREFIX
    prod_before = fingerprint(PROD_DB)
    cli = sync_redis()
    if cli is None:
        print("无法读取 .env 的 REDIS_URL")
        return 1
    try:
        cli.ping()
    except Exception as exc:
        print(f"Redis 未就绪（先执行 bash redis-local.sh run）：{exc}")
        return 1
    formal_keys = cli.keys("dsh:main:*")
    check("正式 Redis 前缀 dsh:main:* 无残留", len(formal_keys) == 0, formal_keys)

    inst_a = Instance("always_speak")
    inst_b = Instance("always_silent")
    INST_A_PREFIX = inst_a.prefix
    ok = False
    try:
        print(f"== 启动隔离实例 A {inst_a.base} (prefix={inst_a.prefix}) ==")
        if not inst_a.start():
            return 1
        token_a = register(inst_a.base, "d5owner")
        run_speak_checks(inst_a.base, token_a)

        print(f"== 启动隔离实例 B {inst_b.base} (prefix={inst_b.prefix}) ==")
        if not inst_b.start():
            return 1
        token_b = register(inst_b.base, "d5silent")
        run_silent_checks(inst_b.base, token_b)
        ok = True
    finally:
        inst_b.stop(cli)
        inst_a.stop(cli)
        try:
            cli.close()
        except Exception:
            pass

    check("正式库全程未被写入（零污染）", fingerprint(PROD_DB) == prod_before,
          f"{prod_before} -> {fingerprint(PROD_DB)}")
    print("\n== 结果 ==")
    print(f"PASS {PASSED} / {PASSED + FAILED}   FAIL {FAILED}")
    if FAILURES:
        print("失败项：" + "、".join(FAILURES))
    return 1 if (FAILED or not ok) else 0


if __name__ == "__main__":
    sys.exit(main())
