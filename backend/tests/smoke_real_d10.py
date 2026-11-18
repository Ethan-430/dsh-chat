"""D10 真实 DeepSeek 冒烟联调（需真实 Key 与网络，不污染正式库/Redis）。

直接驱动 DeepSeekAdapter.turn()，仅构造内存中的消息，不写 DB / Redis。
用途：验证连通性、[SPEAK]/[SILENT] 协议、流式 delta、真实语感。

运行：
  "/c/Program Files/Python314/python.exe" tests/smoke_real_d10.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dsh.deepseek_adapter import DeepSeekAdapter  # noqa: E402
from app.dsh.persona import HARD_RULES  # noqa: E402

PERSONA = {
    "text": (
        "你叫 DSH，是个有点毒舌但讲道理的普通群友。会抬杠、会吐槽、会说不知道，"
        "偶尔冒出一句挺准的观察，但不主动当主持人、不总结、不鼓励大家。"
    ),
    "rules": list(HARD_RULES),
}

SCENARIOS = [
    {
        "name": "被@点名（应开口）",
        "trigger": "mention",
        "recent": [
            {"text": "这家新开的火锅店真不行，排队一小时味道还一般", "sender_type": "human"},
            {"text": "真的？我还想去试试呢", "sender_type": "human"},
            {"text": "@DSH 你说呢，是不是被我们吐槽惨了", "sender_type": "human"},
        ],
    },
    {
        "name": "冷场随口插话（可开口可沉默）",
        "trigger": "silence",
        "recent": [
            {"text": "今天好热啊", "sender_type": "human"},
            {"text": "是啊，空调还坏了", "sender_type": "human"},
        ],
    },
]


async def run_one(adapter: DeepSeekAdapter, sc: dict) -> None:
    print(f"\n=== {sc['name']} (trigger={sc['trigger']}) ===")
    events = []
    async for ev in adapter.turn(
        session_id="smoke-d10",
        trigger=sc["trigger"],
        recent_messages=sc["recent"],
        persona=PERSONA,
        budget={"max_tokens": 120},
    ):
        events.append(ev)
        if "gate" in ev:
            print(f"  [gate] {ev['gate']}")
        elif "delta" in ev:
            print(f"  [delta] {ev['delta']!r}")
        elif "done" in ev:
            done = ev["done"]
            print(f"  [done] should_speak={done['should_speak']} reason={done['reason']}")
            print(f"  [TEXT] {done['text']!r}")
    assert events and "gate" in events[0], "首事件必须是 gate"
    assert any("done" in e for e in events), "必须产出 done"
    done = events[-1].get("done")
    assert done is not None, "末事件必须是 done"
    reason = done["reason"]
    if "deepseek_error" in reason or "deepseek_partial" in reason:
        # 适配器会吞掉异常降级为已读不回；冒烟测试必须把它显式标记为失败，
        # 否则「余额不足 / 网络失败」会被误读成一次正常沉默。
        raise RuntimeError(f"真实 API 调用失败并被降级：{reason}")
    print("  -> 协议 OK, reason =", reason)


async def main() -> None:
    from app.config import settings

    if not settings.deepseek_api_key:
        print("!! DEEPSEEK_API_KEY 未配置，终止")
        return
    adapter = DeepSeekAdapter(
        settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_s=settings.deepseek_timeout_s,
    )
    try:
        for sc in SCENARIOS:
            try:
                await run_one(adapter, sc)
            except Exception as e:  # 单场景失败不阻断其他场景
                print(f"  !! 场景失败：{type(e).__name__}: {e}")
        print("\n真实联调完成。")
    finally:
        await adapter.aclose()


if __name__ == "__main__":
    asyncio.run(main())
