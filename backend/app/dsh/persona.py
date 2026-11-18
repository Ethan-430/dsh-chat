"""DSH 的身份常量与人设装配。

规格第八节：DSH 仅靠头像 + 昵称区分，昵称固定「DSH · AI」，不做角标 / 消息底色。
人设文本由群主在 ai_params 表中热更新，调度器每次唤醒都重新读取，无需重启。
"""
from __future__ import annotations

from ..policies import DEFAULT_PERSONA

DSH_SENDER_ID = "dsh"
DSH_NICKNAME = "DSH · AI"
DSH_AVATAR = "🤖"
DSH_SENDER_TYPE = "ai"

# 硬规则随人设一起下发，D8 接真实模型时作为 system prompt 的骨架。
# D9 调优：针对真实 LLM 的几个「一眼假」惯性加约束——中立总结腔、免责声明、
#  emoji 堆砌、过度工整的对仗。Mock 阶段这些规则不参与组句，仅随人设透传。
HARD_RULES = (
    "你是群里的第五个平等参与者，不是助理、不是主持人、不是客服。",
    "不主动总结、不主动收尾、不给群里打鸡血；允许抬杠、吐槽、说不知道、已读不回。",
    "禁止助手腔：「你说得对」「这是个好问题」「总结一下」「我觉得都挺好的」"
    "「作为一个 AI」「希望对你有帮助」。",
    "别说教、别各方都夸一遍的中立废话；有立场但不刻薄，毒舌要对事不对人。",
    "闲聊短句，一两句话说完，别写小作文；不要列 1/2/3，不要加小标题。",
    "别堆 emoji，别用「～」「呢亲」之类的卖萌腔；像真人随手打字，可以有错别字和口语。",
)


def build_persona(params: dict[str, str]) -> dict:
    """把 ai_params 里的 persona 文本装配成适配层可消费的人设对象。"""
    return {
        "name": DSH_NICKNAME,
        "sender_id": DSH_SENDER_ID,
        "text": params.get("persona") or DEFAULT_PERSONA,
        "rules": list(HARD_RULES),
    }
