"""DeepSeek harness 适配层（D8–D9）。

对外契约与 MockAdapter 完全一致（见 adapter.py）：turn() 异步产出
  gate -> delta* -> done。调度器零改动即可在 Mock / DeepSeek 间切换。

设计要点：
  - 用 openai SDK 的 AsyncOpenAI 走 DeepSeek 的 OpenAI 兼容 /chat/completions 流式接口。
  - 决策协议：模型回复必须以 [SPEAK] 或 [SILENT] 开头；服务端解析并剥离控制符。
    [SILENT] 时绝不产出 delta，避免「已读不回」的内容泄露给客户端。
  - 控制符可能被切成多个 token（如 "[SP" + "EAK]"），故开头用状态机缓冲到能判定为止。
  - 任何 API / 网络异常都降级为「已读不回」，绝不把异常抛给调度器拖垮人类聊天。
  - client 可注入，便于用假 HTTP 客户端做确定性测试（不需要真实 Key / 网络）。
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator

from .adapter import DSHAdapter, TurnDone

log = logging.getLogger("dsh.deepseek")

_SPEAK = "[SPEAK]"
_SILENT = "[SILENT]"
# 语音轮：以语音消息发出（服务端合成 TTS）。留一个带空格的写法容错。
_SPEAK_VOICE_FORMS = ("[SPEAK:VOICE]", "[SPEAK: VOICE]")
_MAX_TOKEN_PREFIX = 64  # 控制符前缀异常长时的保险阈值（正常 1–2 个 token 内判定）

# 「明确要语音」的说法。命中就给本轮加硬指令：让模型自己体会的命中率只有五成左右。
_VOICE_ASK_RE = re.compile(
    r"(发.{0,3}语音|语音(说|回|来|消息|回答)|说句话|说一句|用嘴|念给我|读出来|听听你的声音|让我听听)"
)


def _asked_for_voice(recent_messages: list[dict]) -> bool:
    """最后一条人类发言是否明确点名要语音。"""
    for m in reversed(recent_messages):
        if m.get("sender_type") == "human":
            return bool(_VOICE_ASK_RE.search(str(m.get("text") or "")))
    return False

_TRIGGER_HINT = {
    "mention": "本轮有人直接叫你的名字（@），按人设你必须回一句，绝不沉默——"
               "哪怕只回「博士，我在」，也不能已读不回。",
    "question": "本轮有人抛出了疑问。默认已读不回；只有当你真有温和而具体的见解时才接一句，否则沉默。",
    "ignored": "本轮是你连续被无视后的叫醒。默认继续潜水；真要开口也只平静地轻轻一句，别解释、别讨好。",
    "silence": "本轮群里冷场了一阵。默认已读不回，绝不当主持人救场；只有真有想说的话才随口插一句。",
    "manual": "本轮有人手动叫你出声，温柔简短回一句。",
}

# 模型偶尔会照着上下文里的说话人标注，在正文开头复述「DSH · AI（你自己）：」，
# 或自带角色名前缀。这些都不该出现在聊天窗里，统一剥掉。
_LEADING_LABELS = (
    "DSH · AI（你自己）", "DSH·AI（你自己）", "DSH AI（你自己）", "DSH（你自己）",
    "特蕾西娅（你自己）", "特蕾西娅",
    "DSH · AI", "DSH·AI", "DSH",
    "（你自己）",
)
_LEADING_SEPS = ("：", ":")


def _strip_leading_labels(text: str, final: bool) -> tuple[str, str]:
    """剥离开头可能出现的「名字：」前缀。

    返回 ``(可立即输出的文本, 需继续缓冲的剩余部分)``：剩余为空表示开头已确定，
    可以放心把文本吐出去；剩余非空表示开头仍可能是某个前缀的一部分，需等更多字符。
    """
    t = text.lstrip()
    while True:
        hit = None
        for lab in _LEADING_LABELS:
            for sep in _LEADING_SEPS:
                full = lab + sep
                if t.startswith(full):
                    hit = full
                    break
            if hit:
                break
        if not hit:
            break
        t = t[len(hit):].lstrip()

    # 还没到冒号但已经是某个前缀的真前缀，先别急着输出。
    if not final and t:
        for lab in _LEADING_LABELS:
            for sep in _LEADING_SEPS:
                if (lab + sep).startswith(t):
                    return "", t
    return t, ""


class DeepSeekAdapter:
    """把 DeepSeek 流式输出适配成 DSHAdapter 契约。"""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-chat",
        timeout_s: float = 20.0,
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_s = float(timeout_s)
        # 注入 client 时无需 SDK / Key / 网络，供 e2e 假 HTTP 测试使用
        self._client = client if client is not None else self._build_client()
        self.last_request: dict[str, Any] | None = None  # 最近一次请求参数，便于排障/断言

    async def aclose(self) -> None:
        """关闭 SDK 的底层 HTTP 客户端；Mock 无此法，调度器用 getattr 守护调用。"""
        close = getattr(self._client, "close", None)
        if close:
            try:
                await close()
            except Exception:
                pass

    def _build_client(self) -> Any:
        from openai import AsyncOpenAI  # 延迟导入：未启用 DeepSeek 时不依赖 SDK

        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_s,
            max_retries=1,
        )

    # ---------- 提示词 ----------

    def _system_prompt(self, persona: dict, trigger: str) -> str:
        text = str(persona.get("text") or "").strip()
        rules = [str(r) for r in (persona.get("rules") or []) if str(r).strip()]
        rule_text = "\n".join(f"- {r}" for r in rules) or "- （无人设规则）"
        hint = _TRIGGER_HINT.get(trigger, "")
        # 被 @ 是硬约束：连协议本身都不给 [SILENT] 这个选项，从根上杜绝概率性沉默。
        protocol = (
            (
                "决策协议（必须严格遵守，否则输出作废）：\n"
                f"1. 本轮只能输出 {_SPEAK} 或 {_SPEAK_VOICE_FORMS[0]}，不允许 {_SILENT}；"
                "其后紧跟正文，不要换行也不要解释。\n"
                "2. 哪怕只想得出一句「博士，我在」，也要开口。\n"
                "3. 控制符不会发给群友；正文里也不要再出现它。\n"
                "4. 正文一两句话，口语化，像真人随手打字；不用礼貌套话，不主动总结或收尾。\n"
                "5. 正文直接开口说话，不要以「特蕾西娅：」「DSH：」或任何「名字：」前缀开头，"
                "也不要复述说话人标注。"
            )
            if trigger == "mention"
            else (
                "决策协议（必须严格遵守，否则输出作废）：\n"
                f"1. 回复最开头只能是 {_SPEAK}、{_SPEAK_VOICE_FORMS[0]} 或 {_SILENT} 三者之一，"
                "其后不要换行也不要解释。\n"
                f"2. 决定开口：输出 {_SPEAK} 后紧跟正文；决定用语音说：输出 {_SPEAK_VOICE_FORMS[0]} "
                f"后紧跟要说的话；决定已读不回：只输出 {_SILENT}，不要写任何正文。\n"
                "3. 控制符不会发给群友；正文里也不要再出现它。\n"
                "4. 正文一两句话，口语化，像真人随手打字；不用礼貌套话，不主动总结或收尾。\n"
                "5. 正文直接开口说话，不要以「特蕾西娅：」「DSH：」或任何「名字：」前缀开头，"
                "也不要复述说话人标注。"
            )
        )
        return (
            "你是群聊里的参与者，此刻以特蕾西娅的身份说话——不是助理、不是主持人、不是客服。\n"
            f"人设：{text}\n"
            "硬性规则：\n" + rule_text + "\n"
            + (f"当前情形：{hint}\n" if hint else "")
            + "最重要的一条：真人潜水是常态、发言是例外。被 @ 叫名字时必须回——"
            "这条优先级高于从容寡言，哪怕只回一句「博士，我在」也不能沉默；"
            "其他时候如果你没有一句真的非说不可、能让人心里安一点的话，就选已读不回。"
            "每句话都接是机器人，不是群友——宁可沉默，别刷存在感。\n"
            "上下文是群里最近的发言，按时间从旧到新排列，每条前面写了说话人；最后一条是最新发言，"
            "也就是这次叫醒你要接的话。看清是谁在说、在接谁的话，别当成同一个人在自言自语。\n"
            "语音：把某一句话说出口，比打字更像真人。但只在它很短（40 字以内）、像随口一句时才用 "
            f"{_SPEAK_VOICE_FORMS[0]}；要解释、列举、讲道理的时候用 {_SPEAK} 打字。"
            "语音里不要出现表情符号、颜文字、括号里的动作（会被念成噪音），也不要连着两条都发语音。"
            "例外：对方明确要求「用语音说」「说句话」「发条语音」时，这一条必须用 "
            f"{_SPEAK_VOICE_FORMS[0]}，不能拿打字糊过去。\n"
            + protocol
        )

    def _messages(self, persona: dict, trigger: str, recent_messages: list[dict]) -> list[dict]:
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt(persona, trigger)}
        ]
        for m in recent_messages[-30:]:
            text = str(m.get("text") or "").strip()
            if not text:
                # 人类语音消息：文本为空，但必须让模型知道「有人刚说了句话」，
                # 否则它会把更早的一条当成最新发言，直接接错话。
                status = str(m.get("audio_status") or "none")
                if status in ("ready", "pending"):
                    dur = int(m.get("audio_duration_ms") or 0)
                    secs = max(1, round(dur / 1000)) if dur else 0
                    text = (f"[发来一条 {secs} 秒语音]" if secs else "[发来一条语音]") + \
                           "（你听不到内容，可以请对方打字再说一次）"
                else:
                    continue
            if m.get("sender_type") == "ai":
                # 自己的话就是 assistant 原文，绝不能加「DSH · AI（你自己）：」这类前缀——
                # 加了模型会照着模仿，把标注当成正文一部分复述出来。
                messages.append({"role": "assistant", "content": text})
            else:
                # 人类消息带昵称：否则多人发言会被模型当成同一个人在自言自语，接不上话题。
                name = str(m.get("nickname") or "").strip() or "群友"
                messages.append({"role": "user", "content": f"{name}：{text}"})
        return messages

    # ---------- 流式读取 ----------

    @staticmethod
    def _delta_text(chunk: Any) -> str:
        """兼容 SDK 的 ChatCompletionChunk 与测试里的 dict 形态。"""
        choices = (chunk.get("choices") if isinstance(chunk, dict)
                   else getattr(chunk, "choices", None)) or []
        if not choices:
            return ""
        first = choices[0]
        delta = (first.get("delta") if isinstance(first, dict)
                 else getattr(first, "delta", None))
        if delta is None:
            return ""
        content = (delta.get("content") if isinstance(delta, dict)
                   else getattr(delta, "content", None))
        return content or ""

    async def _stream(self, messages: list[dict], budget: dict) -> AsyncIterator[str]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": max(16, int(budget.get("max_tokens", 120))),
            "temperature": 0.85,
            "top_p": 0.95,
        }
        self.last_request = kwargs
        response = await self._client.chat.completions.create(**kwargs)
        # 真实 SDK 返回 AsyncStream（既是异步上下文管理器也可直接异步迭代）
        if hasattr(response, "__aenter__"):
            async with response as stream:
                async for chunk in stream:
                    text = self._delta_text(chunk)
                    if text:
                        yield text
        else:
            async for chunk in response:
                text = self._delta_text(chunk)
                if text:
                    yield text

    # ---------- DSHAdapter 契约 ----------

    async def turn(
        self,
        *,
        session_id: str,
        trigger: str,
        recent_messages: list[dict],
        persona: dict,
        budget: dict,
    ) -> AsyncIterator[dict]:
        messages = self._messages(persona, trigger, recent_messages)
        if _asked_for_voice(recent_messages):
            # 对方明确点名要语音：在系统提示末尾补一条硬指令。
            # 比让模型自己「体会」靠谱——实测同一句提示下它有一半概率仍然打字，
            # 一旦打字就已经逐字流出去了，事后改成语音会让气泡内容闪变。
            messages[0]["content"] += (
                f"\n本轮特别要求：对方明确要你用语音说话，这一条必须输出 {_SPEAK_VOICE_FORMS[0]}"
                "，不要用打字糊过去。"
            )
        decision: str | None = None       # None / "speak" / "silent"
        voice = False                      # 本轮是否以语音消息发出（[SPEAK:VOICE]）
        head = ""                          # 未判定前缓冲的开头
        collected: list[str] = []          # 已收集的正文（语音轮只收不发）
        prefix_buf = ""                    # 开口后、开头确定前的正文缓冲
        prefix_done = False                # 开头是否已确定，可放心逐字输出

        def _open_body(text: str) -> str:
            """判定开口后处理首批正文：剥离开头的「名字：」前缀。

            返回可立即下发的文本；返回空串表示这批要挂在缓冲里等更多字符。
            """
            nonlocal prefix_buf, prefix_done
            prefix_buf += text
            out, rest = _strip_leading_labels(prefix_buf, False)
            prefix_buf = rest
            if rest:
                return ""            # 开头仍是某前缀的一部分，继续缓冲
            prefix_done = True
            return out

        def _take(text: str) -> str:
            """收下正文。语音轮只进 collected、不产出 delta——语音没法逐字出现。"""
            if not text:
                return ""
            collected.append(text)
            return "" if voice else text

        try:
            async for piece in self._stream(messages, budget):
                if decision is None:
                    head += piece
                    probe = head.lstrip()
                    upper = probe.upper()
                    voice_form = next(
                        (f for f in _SPEAK_VOICE_FORMS if upper.startswith(f)), None
                    )
                    if voice_form:
                        decision, voice = "speak", True
                        rest = probe[len(voice_form):].lstrip()
                        head = ""
                        yield {"gate": "voice"}
                        out = _take(_open_body(rest)) if rest else ""
                        if out:
                            yield {"delta": out}
                    elif upper.startswith(_SPEAK):
                        decision = "speak"
                        rest = probe[len(_SPEAK):].lstrip()
                        head = ""
                        yield {"gate": "speak"}
                        out = _take(_open_body(rest)) if rest else ""
                        if out:
                            yield {"delta": out}
                    elif upper.startswith(_SILENT):
                        decision = "silent"
                        head = ""
                        yield {"gate": "silent"}
                        # [SILENT] 后即便模型又写了内容也不下发
                    elif any(f.startswith(upper) for f in (_SPEAK, _SILENT, *_SPEAK_VOICE_FORMS)):
                        # 控制符被切断（如 "[" / "[SP" / "[SPEAK:"），继续缓冲
                        if len(probe) >= _MAX_TOKEN_PREFIX:
                            decision = "speak"
                            pending, head = head, ""
                            yield {"gate": "speak"}
                            out = _take(_open_body(pending))
                            if out:
                                yield {"delta": out}
                        continue
                    else:
                        # 模型没按协议输出控制符：按开口处理，全文下发
                        decision = "speak"
                        pending, head = head, ""
                        yield {"gate": "speak"}
                        out = _take(_open_body(pending))
                        if out:
                            yield {"delta": out}
                elif decision == "speak":
                    out = _take(_open_body(piece) if not prefix_done else piece)
                    if out:
                        yield {"delta": out}
                # decision == "silent" 时丢弃后续内容
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("DeepSeek 调用失败，降级为已读不回：%s: %s", type(exc).__name__, exc)
            if decision == "speak":
                if not prefix_done:  # 把还挂在缓冲里的正文按「开头已结束」处理
                    out, _ = _strip_leading_labels(prefix_buf, True)
                    prefix_buf = ""
                    prefix_done = True
                    if out:
                        collected.append(out)
                        yield {"delta": out}
                text = "".join(collected).strip()
                yield {"done": TurnDone(
                    text=text, should_speak=bool(text),
                    reason=f"deepseek_partial:{type(exc).__name__}",
                    voice=voice,
                )}
            else:
                if decision is None:
                    yield {"gate": "silent"}
                yield {"done": TurnDone(
                    text="", should_speak=False,
                    reason=f"deepseek_error:{type(exc).__name__}",
                    voice=False,
                )}
            return

        if decision == "speak":
            if not prefix_done:  # 流结束仍挂在缓冲里：按「开头已结束」输出剩余
                out, _ = _strip_leading_labels(prefix_buf, True)
                prefix_buf = ""
                prefix_done = True
                if out:
                    collected.append(out)
                    yield {"delta": out}
            text = "".join(collected).strip()
            yield {"done": TurnDone(
                text=text, should_speak=bool(text),
                reason=f"deepseek:spoke(trigger={trigger})"
                       + (":voice" if voice else ""),
                voice=voice and bool(text),
            )}
        else:
            if decision is None:  # 空流也视为已读不回
                yield {"gate": "silent"}
            yield {"done": TurnDone(
                text="", should_speak=False,
                reason="deepseek:silent(protocol=[SILENT])" if decision == "silent"
                       else "deepseek:silent(empty_stream)",
                voice=False,
            )}
