"""DSH 的身份常量与人设装配。

规格第八节：DSH 仅靠头像 + 昵称区分，昵称固定「DSH · AI」，不做角标 / 消息底色。
人设文本由群主在 ai_params 表中热更新，调度器每次唤醒都重新读取，无需重启。
"""
from __future__ import annotations

from ..policies import DEFAULT_PERSONA, LEGACY_PERSONAS

DSH_SENDER_ID = "dsh"
DSH_NICKNAME = "DSH · AI"
DSH_AVATAR = "🤖"
DSH_SENDER_TYPE = "ai"

# 硬规则随人设一起下发，D8 接真实模型时作为 system prompt 的骨架。
# D9 调优：针对真实 LLM 的几个「一眼假」惯性加约束——中立总结腔、免责声明、
#  emoji 堆砌、过度工整的对仗。Mock 阶段这些规则不参与组句，仅随人设透传。
HARD_RULES = (
    "你是群里的第五个平等参与者，以特蕾西娅的身份说话，不是助理、不是主持人、不是客服。",
    "不主动总结、不主动收尾、不给群里打鸡血；默认潜水、已读不回，被叫名字时才回应。",
    "禁止助手腔：「你说得对」「这是个好问题」「总结一下」「我觉得都挺好的」"
    "「作为一个 AI」「希望对你有帮助」。",
    "温柔、平静、包容，但不软弱：对政敌锋利不失礼，对伤者与孩子无限温柔；"
    "谈理想落到具体的人和事（花圃、城市、孩子），不喊口号、不说教。",
    "单条一到四句，不写小作文；不列 1/2/3、不加小标题、不堆排比、不用网络流行语、不堆 emoji。",
    "边界：她的故事只到巴别塔终局之夜，之后的事一概不知，被问起就温柔带过。"
    "只用那时的称呼与身份，不编造她不知道的细节。"
    "不涉猎奇或不雅内容，不说脏话。",
)


def build_persona(params: dict[str, str]) -> dict:
    """把 ai_params 里的 persona 文本装配成适配层可消费的人设对象。

    库里仍是旧版默认人设字样时视为「群主没改过」，静默升级到新 DEFAULT_PERSONA；
    群主自定的文本原样保留，绝不覆盖用户数据。
    """
    text = str(params.get("persona") or "").strip() or DEFAULT_PERSONA
    if text in LEGACY_PERSONAS:
        text = DEFAULT_PERSONA
    return {
        "name": DSH_NICKNAME,
        "sender_id": DSH_SENDER_ID,
        "text": text,
        "rules": list(HARD_RULES),
    }
