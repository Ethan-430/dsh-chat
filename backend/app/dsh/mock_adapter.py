"""Mock harness：不依赖外部 API，按 trigger 产出符合人设的短句并逐字流式输出。

should_speak 覆盖两类结果：
  - mention / question / ignored / manual：默认开口；
  - silence：50% 已读不回（设计文档第四节），用于验收「不落库、不广播、不动冷却」。

环境变量（仅影响 Mock，便于隔离实例做确定性测试）：
  DSH_MOCK_MODE       normal（默认）| always_speak | always_silent
  DSH_MOCK_DELAY_MS   每个字块的间隔，默认 35ms；测试可设 0
"""
from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator

from .adapter import TurnDone

LEAD_INS: dict[str, tuple[str, ...]] = {
    "mention": (
        "叫我就应，我又不是客服。",
        "在，刚把手机扣过去又翻回来了。",
        "点名就点名，别搞得像开会。",
    ),
    "question": (
        "这问题问得像随堂测，我给个不负责任的直觉：",
        "别指望标准答案，我大概会这么说：",
        "我猜的，错了别找我：",
    ),
    "silence": (
        "突然安静，我随便说一句，别当开场白。",
        "都不说话是等谁救场？那我先摸会儿鱼。",
        "这冷场味儿有点熟，我就不总结了吧。",
    ),
    "ignored": (
        "行，连着几条没人接我，我记下了。",
        "我算是看明白了，这屋里的回音比我话多。",
        "算了，我自己接自己。",
    ),
    "manual": ("嗯，来了。", "说吧，我听着。"),
}

TAILS = (
    "反正明天还得照样聊。",
    "不保证对，但保证不客套。",
    "你们接不接随意。",
    "我先去倒杯水。",
    "这话题再聊下去就要变成汇报了。",
)


class MockAdapter:
    name = "mock"

    def __init__(self, mode: str = "normal", delay_ms: int = 35, seed: int | None = None):
        self.mode = mode if mode in ("normal", "always_speak", "always_silent") else "normal"
        self.delay = max(0, int(delay_ms)) / 1000
        self.rng = random.Random(seed)
        self.calls = 0

    def _decide(self, trigger: str) -> bool:
        if self.mode == "always_silent":
            return False
        if self.mode == "always_speak":
            return True
        if trigger == "silence":
            return self.rng.random() < 0.5
        return True

    def _compose(self, trigger: str, recent_messages: list[dict], max_tokens: int) -> str:
        self.calls += 1
        lead = self.rng.choice(LEAD_INS.get(trigger, LEAD_INS["manual"]))
        tail = self.rng.choice(TAILS)
        # 引述最近一条人类消息，让 mock 的回复和上下文有点关系（纯字符串拼接，不做任何执行）
        snippet = ""
        for m in reversed(recent_messages):
            if m.get("sender_type") == "human":
                raw = " ".join(str(m.get("text") or "").split())
                snippet = raw[:14]
                break
        quote = f"「{snippet}」这事儿吧，" if snippet else ""
        text = f"{quote}{lead}{tail}"
        # token 预算的粗略映射：中文按约 2 字符 / token 估算，给短句留足空间
        limit = max(16, int(max_tokens) * 2)
        return text[:limit]

    async def turn(
        self,
        *,
        session_id: str,
        trigger: str,
        recent_messages: list[dict],
        persona: dict,
        budget: dict,
    ) -> AsyncIterator[dict]:
        should_speak = self._decide(trigger)
        yield {"gate": "speak" if should_speak else "silent"}

        if not should_speak:
            reason = "这轮没什么想接的，已读不回" if trigger == "silence" else "mock 强制沉默"
            yield {"done": TurnDone(text="", should_speak=False, reason=reason)}
            return

        text = self._compose(trigger, recent_messages, int(budget.get("max_tokens", 120)))
        # 逐字（1–3 字符一块）yield，模拟真实 harness 的 token 流
        i = 0
        while i < len(text):
            step = self.rng.choice((2, 3, 3, 4))
            piece = text[i:i + step]
            i += len(piece)
            if self.delay:
                await asyncio.sleep(self.delay)
            yield {"delta": piece}

        yield {"done": TurnDone(text=text, should_speak=True, reason=f"trigger={trigger}")}
