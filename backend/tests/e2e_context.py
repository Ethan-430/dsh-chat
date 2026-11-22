#!/usr/bin/env python
"""上下文回归测试：确保 DSH 读到的是「最近的、带说话人」的群聊上下文。

用法：
    cd backend && python tests/e2e_context.py

锁定两个曾导致「接不上话题」的 bug：
  1. 调度器误用 get_history（ASC+LIMIT）——消息超过 30 条后拿到的全是开群最早消息；
  2. 适配层丢弃 nickname——多人发言被压成无名 user 消息，指代与话题归属全丢。

不写正式库，不连 Redis / 网络 / 真实 Key（临时 SQLite + 假 HTTP client）。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.dsh.deepseek_adapter import DeepSeekAdapter  # noqa: E402
from app.policies import ai_param_defaults  # noqa: E402
from app.scheduler import DshScheduler  # noqa: E402

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


PERSONA = {
    "name": "DSH · AI", "sender_id": "dsh",
    "text": "毒舌但讲道理的群友", "rules": ["不主动总结", "短句"],
}
RECENT = [
    {"sender_type": "human", "nickname": "甲", "text": "周末去哪玩"},
    {"sender_type": "ai", "nickname": "DSH · AI", "text": "家里蹲挺好的"},
    {"sender_type": "human", "nickname": "乙", "text": "你说呢？"},
]


class FakeCompletions:
    def __init__(self):
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        async def empty():
            if False:
                yield {}

        return empty()


async def test_recent_history() -> None:
    print("\n== 历史消息读取区间 ==")
    old_path = settings.sqlite_path
    with tempfile.TemporaryDirectory() as tmp:
        settings.sqlite_path = str(Path(tmp) / "context-test.db")
        try:
            await db.init_db()
            for i in range(45):
                await db.insert_message(
                    room_id="main", sender_type="human", sender_id=f"u{i % 3}",
                    nickname=f"用户{i % 3}", avatar=None, text=f"第{i}条",
                )

            recent = await db.get_recent_history("main", limit=30)
            texts = [m["text"] for m in recent]
            check("超过 limit 时取到最近 30 条", len(texts) == 30 and texts[0] == "第15条", texts[:3])
            check("最近 30 条按时间正序（最新在最后）",
                  texts == [f"第{i}条" for i in range(15, 45)], texts[-3:])
            check("最新一条就是当前话题", texts[-1] == "第44条", texts[-1])

            early = await db.get_history("main", limit=30)
            check("首屏 get_history 语义不变（仍取最早 30 条）",
                  early[0]["text"] == "第0条" and early[-1]["text"] == "第29条")

            # 端到端组合：调度器取到的历史，喂给适配层后模型应看到最近话题
            comp = FakeCompletions()
            adapter = DeepSeekAdapter("fake-key", client=type(
                "Client", (), {"chat": type("Chat", (), {"completions": comp})()}
            )())
            async for _ in adapter.turn(
                session_id="dsh", trigger="mention", recent_messages=recent,
                persona=PERSONA, budget={"max_tokens": 60},
            ):
                pass
            sent = comp.calls[0]["messages"]
            check("模型上下文不含开群旧话题",
                  "第0条" not in sent[-1]["content"] and "第44条" in sent[-1]["content"],
                  sent[-1]["content"])
        finally:
            settings.sqlite_path = old_path


class FakeRedis:
    """调度器所需的 Redis 最小接口，纯内存，避免依赖本机 Redis 服务。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def pipeline(self):
        outer = self

        class Pipe:
            def __init__(self):
                self.ops: list[tuple] = []

            def set(self, key, value, **kwargs):
                self.ops.append(("set", key, value))
                return self

            def incr(self, key):
                self.ops.append(("incr", key))
                return self

            async def execute(self):
                out = []
                for op in self.ops:
                    if op[0] == "set":
                        outer.store[op[1]] = op[2]
                        out.append(True)
                    else:
                        n = int(outer.store.get(op[1], 0)) + 1
                        outer.store[op[1]] = str(n)
                        out.append(n)
                return out

        return Pipe()

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, **kwargs):
        self.store[key] = value
        return True

    async def incr(self, key):
        n = int(self.store.get(key, 0)) + 1
        self.store[key] = str(n)
        return n

    async def expire(self, key, seconds):
        return True

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    async def ping(self):
        return True


class FakeSio:
    def __init__(self):
        self.events: list[tuple] = []

    async def emit(self, event, payload, **kwargs):
        self.events.append((event, payload))


async def test_scheduler_context() -> None:
    print("\n== 调度器整链路（无需 Redis 服务） ==")
    old_path = settings.sqlite_path
    old_interval = settings.dsh_delta_interval_ms
    settings.dsh_delta_interval_ms = 0
    with tempfile.TemporaryDirectory() as tmp:
        settings.sqlite_path = str(Path(tmp) / "scheduler-context.db")
        try:
            await db.init_db()
            for i in range(45):
                await db.insert_message(
                    room_id="main", sender_type="human", sender_id=f"u{i % 3}",
                    nickname=f"用户{i % 3}", avatar=None, text=f"话题{i}",
                )

            comp = FakeCompletions()
            adapter = DeepSeekAdapter("fake-key", client=type(
                "Client", (), {"chat": type("Chat", (), {"completions": comp})()}
            )())
            sched = DshScheduler(
                FakeSio(), adapter=adapter, redis_client=FakeRedis(),
                room_id="main", prefix="context-test",
            )
            await sched._run_turn("mention", ai_param_defaults())

            check("调度器确实调用了模型", len(comp.calls) == 1, len(comp.calls))
            sent = comp.calls[0]["messages"]
            all_text = " ".join(m["content"] for m in sent)
            check("45 条历史下模型看不到开群旧话题", "话题0" not in all_text,
                  all_text[:120])
            check("模型看到的是最近话题", "话题44" in sent[-1]["content"],
                  sent[-1]["content"])
            check("最新发言带说话人昵称",
                  sent[-1]["content"].startswith("用户") and "：话题44" in sent[-1]["content"],
                  sent[-1]["content"])
            check("上下文条数受 recent_limit 约束", len(sent) <= settings.dsh_recent_limit + 1,
                  len(sent))
        finally:
            settings.sqlite_path = old_path
            settings.dsh_delta_interval_ms = old_interval


def test_speaker_labels() -> None:
    print("\n== 上下文说话人标注 ==")
    comp = FakeCompletions()
    adapter = DeepSeekAdapter("fake-key", client=type(
        "Client", (), {"chat": type("Chat", (), {"completions": comp})()}
    )())
    msgs = adapter._messages(PERSONA, "mention", RECENT)

    check("人类消息带昵称，不再是无名独白",
          msgs[1]["content"] == "甲：周末去哪玩", msgs[1]["content"])
    check("AI 自己的消息是纯正文，不带「DSH · AI（你自己）：」前缀",
          msgs[2]["content"] == "家里蹲挺好的", msgs[2]["content"])
    check("最后一条是群里最新发言",
          msgs[-1]["content"] == "乙：你说呢？", msgs[-1]["content"])
    check("角色映射仍为 human->user / ai->assistant",
          [m["role"] for m in msgs] == ["system", "user", "assistant", "user"],
          [m["role"] for m in msgs])
    sys_text = msgs[0]["content"]
    check("system 明确上下文按时间正序、最后一条为本次要接的话",
          "最后一条是最新发言" in sys_text and "说话人" in sys_text)
    check("system 保留决策协议与 @ 必回",
          "[SPEAK]" in sys_text and "[SILENT]" in sys_text and "叫你的名字" in sys_text)

    fallback = adapter._messages(PERSONA, "mention", [{"text": "你好", "sender_type": "human"}])
    check("缺昵称时回退为「群友」", fallback[1]["content"] == "群友：你好", fallback[1]["content"])
    empty_name = adapter._messages(PERSONA, "mention", [
        {"text": "嗯", "sender_type": "ai", "nickname": ""}
    ])
    check("AI 缺昵称时也是纯正文，不回退加前缀",
          empty_name[1]["content"] == "嗯", empty_name[1]["content"])


async def async_main() -> None:
    await test_recent_history()
    await test_scheduler_context()
    test_speaker_labels()


def main() -> int:
    asyncio.run(async_main())
    print(f"\n===== {PASSED} PASS / {FAILED} FAIL =====")
    if FAILURES:
        print("失败项：", "、".join(FAILURES))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
