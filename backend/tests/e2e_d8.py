#!/usr/bin/env python
"""D8–D9 专项测试：DeepSeek 适配层（假 HTTP 客户端，无需真实 Key / 网络 / Redis）。

用法：
    python backend/tests/e2e_d8.py

用注入的假 client 模拟 OpenAI 兼容流式响应，覆盖：
  - get_adapter() 工厂选择（auto / mock / deepseek，Key 缺失降级）
  - [SPEAK] / [SILENT] 决策协议 + 控制符剥离（含被切成多 token 的情况）
  - 已读不回时绝不产出 delta
  - 无控制符时兜底当作开口
  - API 异常降级为已读不回（gate 前 / gate 后两种）
  - 空流、请求参数（model/max_tokens/messages 角色映射）、isinstance 契约
  - 正式库零污染（只 import，不写库）
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.dsh import get_adapter  # noqa: E402
from app.dsh.adapter import DSHAdapter  # noqa: E402
from app.dsh.deepseek_adapter import DeepSeekAdapter  # noqa: E402
from app.dsh.mock_adapter import MockAdapter  # noqa: E402

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


def fingerprint(path: Path) -> tuple:
    if not path.exists():
        return ("missing",)
    st = path.stat()
    return (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())


# ---------- 假 HTTP 客户端 ----------
def chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


class FakeStream:
    """模拟 SDK 的 AsyncStream：既是异步上下文管理器，也可直接异步迭代。"""

    def __init__(self, pieces: list[str], delay: float = 0):
        self.pieces = pieces
        self.delay = delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for piece in self.pieces:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk(piece)


class FakeCompletions:
    def __init__(self, pieces=None, *, error: Exception | None = None,
                 error_after: int | None = None, delay: float = 0):
        self.pieces = pieces or []
        self.error = error
        self.error_after = error_after
        self.delay = delay
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None and self.error_after is None:
            raise self.error
        stream = FakeStream(self.pieces, self.delay)
        if self.error is not None and self.error_after is not None:
            pieces = self.pieces

            async def gen_then_error():
                for i, piece in enumerate(pieces):
                    yield chunk(piece)
                    if i + 1 == self.error_after:
                        raise self.error

            return gen_then_error()
        return stream


class FakeClient:
    def __init__(self, completions: FakeCompletions):
        self.chat = type("Chat", (), {"completions": completions})()


def make_adapter(pieces=None, **kw) -> tuple[DeepSeekAdapter, FakeCompletions]:
    comp = FakeCompletions(pieces, **kw)
    return DeepSeekAdapter("fake-key", client=FakeClient(comp)), comp


PERSONA = {
    "name": "DSH · AI", "sender_id": "dsh",
    "text": "毒舌但讲道理的群友", "rules": ["不主动总结", "短句"],
}
RECENT = [
    {"sender_type": "human", "nickname": "甲", "text": "周末去哪玩"},
    {"sender_type": "ai", "nickname": "DSH · AI", "text": "家里蹲挺好的"},
    {"sender_type": "human", "nickname": "乙", "text": "你说呢？"},
]
BUDGET = {"max_tokens": 60, "max_latency_ms": 8000}


async def run_turn(adapter, trigger="question"):
    out = []
    async for part in adapter.turn(
        session_id="dsh", trigger=trigger, recent_messages=RECENT,
        persona=PERSONA, budget=BUDGET,
    ):
        out.append(part)
    return out


def split(parts, key):
    return [p[key] for p in parts if key in p]


# ---------- 用例 ----------
def test_factory():
    print("\n== get_adapter() 工厂选择 ==")
    old_key, old_choice = settings.deepseek_api_key, settings.dsh_adapter
    try:
        settings.deepseek_api_key = ""
        settings.dsh_adapter = "auto"
        check("auto + 无 Key -> MockAdapter", isinstance(get_adapter(), MockAdapter))

        settings.dsh_adapter = "mock"
        settings.deepseek_api_key = "sk-anything"
        check("mock 强制 -> MockAdapter", isinstance(get_adapter(), MockAdapter))

        settings.dsh_adapter = "auto"
        check("auto + 有 Key -> DeepSeekAdapter", isinstance(get_adapter(), DeepSeekAdapter))

        settings.dsh_adapter = "deepseek"
        settings.deepseek_api_key = "sk-forced-fake"
        check("deepseek 强制 -> DeepSeekAdapter", isinstance(get_adapter(), DeepSeekAdapter))
    finally:
        settings.deepseek_api_key, settings.dsh_adapter = old_key, old_choice


async def test_speak():
    print("\n== [SPEAK] 开口 + 控制符剥离 ==")
    adapter, comp = make_adapter(["[SPEAK]", "别问了，", "我选躺平。"])
    parts = await run_turn(adapter)
    gates = split(parts, "gate")
    deltas = "".join(split(parts, "delta"))
    done = split(parts, "done")
    check("产出唯一 gate=speak", gates == ["speak"], gates)
    check("delta 拼出正文", deltas == "别问了，我选躺平。", deltas)
    check("正文不含控制符", "[SPEAK]" not in deltas and "[SILENT]" not in deltas, deltas)
    check("done.should_speak=True", bool(done) and done[0]["should_speak"] is True, done)
    check("done.text 与 delta 一致", bool(done) and done[0]["text"] == deltas, done)
    check("done 在最后", bool(parts) and "done" in parts[-1], parts[-1] if parts else "")


async def test_split_token():
    print("\n== 控制符被切成多 token ==")
    adapter, _ = make_adapter(["[SP", "EAK]", "行吧。"])
    parts = await run_turn(adapter)
    deltas = "".join(split(parts, "delta"))
    check("切分的 [SPEAK] 仍被剥离", "[SP" not in deltas and "EAK]" not in deltas and deltas == "行吧。", deltas)
    check("gate=speak", split(parts, "gate") == ["speak"], split(parts, "gate"))


async def test_silent():
    print("\n== [SILENT] 已读不回 ==")
    adapter, _ = make_adapter(["[SILENT]", "这句不该被看到"])
    parts = await run_turn(adapter)
    gates = split(parts, "gate")
    deltas = split(parts, "delta")
    done = split(parts, "done")
    check("gate=silent", gates == ["silent"], gates)
    check("已读不回绝不产出 delta", deltas == [], deltas)
    check("done.should_speak=False", bool(done) and done[0]["should_speak"] is False, done)
    check("done.text 为空", bool(done) and done[0]["text"] == "", done)
    check("reason 标记 silent", bool(done) and "silent" in done[0]["reason"], done)


async def test_no_control_token():
    print("\n== 无控制符兜底 ==")
    adapter, _ = make_adapter(["模型不听话，", "直接输出正文"])
    parts = await run_turn(adapter)
    deltas = "".join(split(parts, "delta"))
    check("无控制符当作开口且全文下发",
          split(parts, "gate") == ["speak"] and deltas == "模型不听话，直接输出正文", (split(parts, "gate"), deltas))


async def test_empty_stream():
    print("\n== 空流 ==")
    adapter, _ = make_adapter([])
    parts = await run_turn(adapter)
    done = split(parts, "done")
    check("空流判定为已读不回",
          split(parts, "gate") == ["silent"] and bool(done) and done[0]["should_speak"] is False, parts)


async def test_errors():
    print("\n== API 异常降级 ==")
    adapter, _ = make_adapter(error=RuntimeError("boom"))
    parts = await run_turn(adapter)
    done = split(parts, "done")
    check("gate 前异常 -> silent + error done",
          split(parts, "gate") == ["silent"] and bool(done)
          and done[0]["should_speak"] is False and "RuntimeError" in done[0]["reason"], parts)

    adapter, _ = make_adapter(["[SPEAK]", "说了一半", "没了"], error=RuntimeError("cut"), error_after=2)
    parts = await run_turn(adapter)
    done = split(parts, "done")
    check("开口后异常 -> 保留已产出正文",
          bool(done) and done[0]["should_speak"] is True and done[0]["text"] == "说了一半", parts)


async def test_request_contract():
    print("\n== 请求参数与消息构造 ==")
    adapter, comp = make_adapter(["[SPEAK]", "收到"])
    parts = await run_turn(adapter, trigger="mention")
    check("isinstance DSHAdapter 协议", isinstance(adapter, DSHAdapter))
    check("adapter.name=deepseek", adapter.name == "deepseek")
    check("恰好一次 create 调用", len(comp.calls) == 1, len(comp.calls))
    kw = comp.calls[0]
    check("stream=True", kw.get("stream") is True, kw.get("stream"))
    check("model 来自配置", kw.get("model") == settings.deepseek_model, kw.get("model"))
    check("max_tokens 来自 budget", kw.get("max_tokens") == BUDGET["max_tokens"], kw.get("max_tokens"))
    msgs = kw.get("messages", [])
    check("system 提示含决策协议与 @ 必回",
          bool(msgs) and msgs[0]["role"] == "system"
          and "[SPEAK]" in msgs[0]["content"] and "叫你的名字" in msgs[0]["content"],
          msgs[0] if msgs else "")
    roles = [m["role"] for m in msgs]
    check("历史角色映射 human->user / ai->assistant",
          roles == ["system", "user", "assistant", "user"], roles)
    sys_text = msgs[0]["content"]
    check("system 注入人设文本与硬规则",
          "毒舌但讲道理的群友" in sys_text and "不主动总结" in sys_text, "")
    check("mock 与 deepseek 契约一致（同一 turn 签名）",
          hasattr(MockAdapter.turn, "__call__") and hasattr(DeepSeekAdapter.turn, "__call__"))


async def async_main():
    test_factory()
    await test_speak()
    await test_split_token()
    await test_silent()
    await test_no_control_token()
    await test_empty_stream()
    await test_errors()
    await test_request_contract()


def main() -> int:
    prod = ROOT / "data" / "dsh_chat.db"
    before = fingerprint(prod)
    asyncio.run(async_main())
    check("正式库全程零污染", fingerprint(prod) == before, f"{before} -> {fingerprint(prod)}")
    print("\n== 结果 ==")
    print(f"PASS {PASSED} / {PASSED + FAILED}   FAIL {FAILED}")
    if FAILURES:
        print("失败项：" + "、".join(FAILURES))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
