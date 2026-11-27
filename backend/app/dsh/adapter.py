"""DSH 适配层契约（规格第三节 / 设计第四节）。

主链路只依赖这里的接口，harness 是 HTTP / CLI / SDK 都只是适配层内部实现。
D5–D7 用 MockAdapter 跑通全链路；D8–D9 接真实 harness 时只换 dsh/__init__.py 的工厂。

turn() 产出两类 chunk（可混合）：
  - {"gate": "speak" | "silent"}   可选的首块决策标记，供服务端决定是否转发 delta
  - {"delta": str}                 逐字片段（只有 gate != "silent" 时服务端才广播）
  - {"done": {"text": str, "should_speak": bool, "reason": str}}

约定：若本轮 should_speak=false，适配器不应产出正文 delta（只给 gate=silent + done），
这样服务端不会把「已读不回」的内容提前泄露给客户端。
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol, TypedDict, runtime_checkable


class TurnDone(TypedDict):
    text: str
    should_speak: bool
    reason: str
    # True 表示这一轮要以「语音消息」发出（模型用了 [SPEAK:VOICE]）。
    # 可选字段：老适配器不返回也不影响。
    voice: bool


@runtime_checkable
class DSHAdapter(Protocol):
    name: str

    async def turn(
        self,
        *,
        session_id: str,
        trigger: str,            # "mention" | "question" | "silence" | "ignored" | "manual"
        recent_messages: list[dict],
        persona: dict,
        budget: dict,            # {"max_tokens": int, "max_latency_ms": int}
    ) -> AsyncIterator[dict]:
        """异步产出 delta / done；由调度器负责超时、广播与落库。"""
        ...
