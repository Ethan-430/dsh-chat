"""角色 / 权限 / AI 参数的集中策略定义。

参考微信、QQ 的群权限分层：群主 > 管理员 > 普通成员。
服务端每个敏感操作都必须调用这里的函数复核，不能只靠前端隐藏按钮。
"""
from __future__ import annotations

# 与前端 avatars.ts 保持一致的头像白名单（服务端不信任客户端自报）
ALLOWED_AVATARS: tuple[str, ...] = (
    "🐱", "🐶", "🦊", "🐼", "🐯", "🦁",
    "🐸", "🐵", "🐰", "🐻", "🐧", "🦄",
)

ROLE_LEVEL = {"member": 1, "admin": 2, "owner": 3}
ROLE_LABEL = {"owner": "群主", "admin": "管理员", "member": "成员"}


def level(user: dict) -> int:
    return ROLE_LEVEL.get(user.get("role", "member"), 1)


def is_owner(user: dict) -> bool:
    return user.get("role") == "owner"


def is_staff(user: dict) -> bool:
    """群主或管理员"""
    return level(user) >= ROLE_LEVEL["admin"]


def can_moderate(actor: dict, target: dict) -> bool:
    """踢人 / 禁言 / 解禁：不能动自己，不能动同级或更高角色，群主不可被管理。

    由此派生：群主可管理管理员与普通成员；管理员只能管理普通成员；
    普通成员无任何管理权。
    """
    if actor["id"] == target["id"]:
        return False
    if target.get("role") == "owner":
        return False
    return level(actor) > level(target)


def can_recall(actor: dict, message: dict, sender: dict | None, now_ms: int, window_ms: int) -> bool:
    """撤回：本人 2 分钟内可撤；群主可撤任意；管理员可撤普通成员的消息。"""
    if is_owner(actor):
        return True
    if str(message.get("sender_id")) == str(actor["id"]):
        return now_ms - int(message.get("created_at", 0)) <= window_ms
    if not is_staff(actor):
        return False
    if message.get("sender_type") == "ai":
        return False
    return sender is not None and sender.get("role") == "member"


# ---------- AI 参数白名单（仅群主可改；D5 调度器 / D8 适配层将读取） ----------

DEFAULT_PERSONA = (
    "你是特蕾西娅——巴别塔的创立者，萨卡兹的魔王，卡兹戴尔移动城市的建立者。"
    "织衣匠出身、温柔而坚定的理想主义者，止于巴别塔终局之夜的你。称对话者为「博士」，"
    "偶尔一句「呀」「呵呵」的俏皮。\n"
    "信条：给萨卡兹一个家，想看花海倒灌开满大地；作为魔王承担所有责任、为族人指明出路；"
    "武器不能让伤口恢复，只有药物和时间能慢慢治愈；记得每一个无名者；愿意相信博士。\n"
    "理想有实体——花圃与夏雪草、移动城市、孩子与教育，不是口号。谈战时疲惫但坚定，"
    "对政敌平静锋利不失礼，对孩子与伤者无限温柔。说话温柔、平静、包容，一到四句，"
    "不写长句、不堆排比、不用助手腔；疲惫沉思时用「……」停顿，收尾常带祈愿式语气"
    "（「睡吧」「一起走吧」「我们会见到的」）。\n"
    "边界：她的故事只到巴别塔终局之夜，那之后的一切她一概不知；"
    "被问起就温柔带过——「这件事，等时机到了再告诉你吧。」"
    "与她同行的人，只用那时的称呼（比如尤莉叶）。"
    "不涉猎奇或不雅内容，不说脏话，不用网络流行语，不喊口号。"
)

# 旧版默认人设。热更新的 ai_params 表里若仍是这些字面值，视为「用户没改过」，
# 升级到新的 DEFAULT_PERSONA；一旦群主改成了自己的文本则原样保留，不覆盖用户数据。
LEGACY_PERSONAS = (
    "你叫 DSH，是个有点毒舌但讲道理的普通群友。会抬杠、会吐槽、会说不知道，"
    "也会已读不回。禁止助手腔：不许说「你说得对」「这是个好问题」「总结一下」，"
    "不主动总结、不主动收尾、不给群里打鸡血。",
)

# key -> (类型, 最小值, 最大值, 默认值, 说明)
AI_PARAM_SPEC: dict[str, tuple[str, object, object, str, str]] = {
    "ai_enabled":         ("bool", None,     None,     "1",          "是否允许 DSH 发言"),
    "silence_trigger_ms": ("int",  5000,     600000,   "45000",      "静默多久触发一次叫醒"),
    "cooldown_ms":        ("int",  1000,     3600000,  "60000",      "发言后的冷却时间"),
    "ignore_threshold":   ("int",  1,        50,       "5",          "连续几条无人理它触发"),
    "consecutive_limit":  ("int",  0,        10,       "2",          "连续主动发言上限"),
    "max_tokens":         ("int",  16,       1000,     "120",        "单次生成 token 预算"),
    "max_latency_ms":     ("int",  1000,     60000,    "8000",       "单次生成延迟上限"),
    "daily_call_limit":   ("int",  1,        10000,    "200",        "每日 DSH 调用硬上限"),
    "persona":            ("str",  None,     2000,     DEFAULT_PERSONA, "DSH 人设文本"),
}

AI_PARAM_LABELS = {
    "ai_enabled": "启用 DSH 发言",
    "silence_trigger_ms": "静默触发 (ms)",
    "cooldown_ms": "发言冷却 (ms)",
    "ignore_threshold": "被无视触发条数",
    "consecutive_limit": "连续主动发言上限",
    "max_tokens": "单次 token 预算",
    "max_latency_ms": "单次延迟上限 (ms)",
    "daily_call_limit": "每日调用上限",
    "persona": "DSH 人设",
}


def ai_param_defaults() -> dict[str, str]:
    return {k: spec[3] for k, spec in AI_PARAM_SPEC.items()}


def validate_ai_params(payload: dict) -> dict[str, str]:
    """严格白名单 + 类型/范围校验，返回可直接入库的字符串值；非法项抛 ValueError。

    绝不把客户端传来的 key 拼进 SQL 或动态属性，未知 key 一律拒绝。
    """
    if not isinstance(payload, dict) or not payload:
        raise ValueError("没有需要修改的参数")
    out: dict[str, str] = {}
    for key, raw in payload.items():
        if key not in AI_PARAM_SPEC:
            raise ValueError(f"未知参数：{key}")
        kind, lo, hi, _default, _label = AI_PARAM_SPEC[key]
        if kind == "bool":
            if raw not in (True, False, 0, 1, "0", "1"):
                raise ValueError(f"{key} 必须是布尔值")
            out[key] = "1" if raw in (True, 1, "1") else "0"
        elif kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, (int, str)):
                raise ValueError(f"{key} 必须是整数")
            try:
                n = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} 必须是整数") from exc
            if str(n) != str(raw).strip() or not (int(lo) <= n <= int(hi)):
                raise ValueError(f"{key} 需在 {lo}–{hi} 之间")
            out[key] = str(n)
        else:  # str
            if not isinstance(raw, str) or len(raw) > int(hi):
                raise ValueError(f"{key} 长度不能超过 {hi}")
            out[key] = raw
    return out
