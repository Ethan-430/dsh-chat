"""D10 语感探针：批量真实场景，统计 DSH 开口/沉默分布与实际语感（充值后用）。

与 smoke_real_d10.py 的区别：
  - smoke：协议冒烟，少量场景，断言必须过；
  - probe：语感探针，场景多，只观察不硬断言，用于判断 persona/温度是否要调。

纯内存消息，不写库/Redis。运行：
  "/c/Program Files/Python314/python.exe" tests/probe_d10.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.dsh.deepseek_adapter import DeepSeekAdapter  # noqa: E402
from app.dsh.persona import build_persona  # noqa: E402

PERSONA = build_persona({})  # 用运行时真实人设，与线上一致

# expect 仅作观察标注，不是断言：<必回> 期望开口，<可沉默> 期望可能已读不回
SCENARIOS = [
    {"name": "@点名-吐槽火锅", "trigger": "mention", "expect": "必回",
     "recent": [
         {"text": "这家新开的火锅店真不行，排队一小时味道还一般", "sender_type": "human"},
         {"text": "@DSH 你说呢", "sender_type": "human"},
     ]},
    {"name": "冷场-空调坏了", "trigger": "silence", "expect": "可沉默",
     "recent": [
         {"text": "今天好热啊", "sender_type": "human"},
         {"text": "是啊，空调还坏了", "sender_type": "human"},
     ]},
    {"name": "无关话题-猫吐了", "trigger": "silence", "expect": "可沉默",
     "recent": [
         {"text": "我家猫今天又吐了", "sender_type": "human"},
         {"text": "是不是换粮换太急了", "sender_type": "human"},
     ]},
    {"name": "群通知-明天团建", "trigger": "silence", "expect": "可沉默",
     "recent": [
         {"text": "明天团建别忘了，9点公司门口集合", "sender_type": "human"},
         {"text": "收到", "sender_type": "human"},
         {"text": "收到", "sender_type": "human"},
     ]},
    {"name": "疑问句-抛给群里", "trigger": "question", "expect": "可沉默",
     "recent": [
         {"text": "周末有人想去看电影吗，最近那部评分挺高的", "sender_type": "human"},
         {"text": "我可能去不了", "sender_type": "human"},
     ]},
    {"name": "多人闲聊-午餐吃什么", "trigger": "silence", "expect": "可沉默",
     "recent": [
         {"text": "中午吃什么啊", "sender_type": "human"},
         {"text": "随便吧，食堂算了", "sender_type": "human"},
         {"text": "楼下那家新开的面馆还行", "sender_type": "human"},
     ]},
    {"name": "被无视后叫醒", "trigger": "ignored", "expect": "可沉默",
     "recent": [
         {"text": "这事儿真挺离谱的", "sender_type": "human"},
         {"text": "DSH 在吗，出来聊两句", "sender_type": "human"},
     ]},
    {"name": "手动叫出声", "trigger": "manual", "expect": "必回",
     "recent": [
         {"text": "DSH 说句话", "sender_type": "human"},
     ]},
]


async def run_one(adapter: DeepSeekAdapter, sc: dict) -> tuple[str, str, str]:
    gate, text, reason = "?", "", "?"
    try:
        async for ev in adapter.turn(
            session_id="probe-d10",
            trigger=sc["trigger"],
            recent_messages=sc["recent"],
            persona=PERSONA,
            budget={"max_tokens": 120},
        ):
            if "gate" in ev:
                gate = ev["gate"]
            elif "done" in ev:
                text = ev["done"]["text"]
                reason = ev["done"]["reason"]
    except Exception as exc:
        reason = f"EXC:{type(exc).__name__}:{exc}"
    return gate, text, reason


async def main() -> None:
    if not settings.deepseek_api_key:
        print("!! DEEPSEEK_API_KEY 未配置，终止")
        return
    adapter = DeepSeekAdapter(
        settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        timeout_s=settings.deepseek_timeout_s,
    )
    results = []
    try:
        for sc in SCENARIOS:
            gate, text, reason = await run_one(adapter, sc)
            results.append((sc, gate, text, reason))
            print(f"\n[{sc['name']}] expect={sc['expect']} trigger={sc['trigger']}")
            print(f"  gate={gate}  reason={reason}")
            print(f"  TEXT={text!r}")
    finally:
        await adapter.aclose()

    speak = sum(1 for _, g, _, _ in results if g == "speak")
    silent = sum(1 for _, g, _, _ in results if g == "silent")
    err = sum(1 for _, _, _, r in results if "deepseek_error" in r or "EXC" in r)
    print(f"\n===== 统计：speak={speak}  silent={silent}  error={err}  共={len(results)} =====")
    must = [(sc, g) for sc, g, _, _ in results if sc["expect"] == "必回"]
    print("必回场景:", [(sc["name"], g) for sc, g in must])
    optional = [(sc, g) for sc, g, _, _ in results if sc["expect"] == "可沉默"]
    print("可沉默场景:", [(sc["name"], g) for sc, g in optional])


if __name__ == "__main__":
    asyncio.run(main())
