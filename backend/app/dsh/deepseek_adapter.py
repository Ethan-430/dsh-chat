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
from typing import Any, AsyncIterator

from .adapter import DSHAdapter, TurnDone

log = logging.getLogger("dsh.deepseek")

_SPEAK = "[SPEAK]"
_SILENT = "[SILENT]"
_MAX_TOKEN_PREFIX = 64  # 控制符前缀异常长时的保险阈值（正常 1–2 个 token 内判定）

_TRIGGER_HINT = {
    "mention": "本轮有人直接叫你的名字（@），按人设你必须回一句，不要沉默。",
    "question": "本轮有人抛出了疑问。默认已读不回；只有当你真有独到又好笑的看法时才接一句，否则沉默。",
    "ignored": "本轮是你连续被无视后的叫醒。默认继续沉默；真要开口也只自嘲一句，别解释、别讨好。",
    "silence": "本轮群里冷场了一阵。默认已读不回，绝不当主持人救场；只有真有想吐槽的才随口插一句。",
    "manual": "本轮有人手动叫你出声，简短回一句。",
}


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
        return (
            "你是群聊里的第五个真人参与者 DSH，不是助理、不是主持人、不是客服。\n"
            f"人设：{text}\n"
            "硬性规则：\n" + rule_text + "\n"
            + (f"当前情形：{hint}\n" if hint else "")
            + "最重要的一条：真人潜水是常态、发言是例外。被 @ 叫名字时才必须回；"
            "其他时候如果你没有一句真的非说不可、能让群友笑一下或点个头的话，就选已读不回。"
            "每句话都接是机器人，不是群友——宁可沉默，别刷存在感。\n"
            "决策协议（必须严格遵守，否则输出作废）：\n"
            f"1. 回复最开头只能是 {_SPEAK} 或 {_SILENT} 二者之一，其后不要换行也不要解释。\n"
            f"2. 决定开口：输出 {_SPEAK} 后紧跟正文；决定已读不回：只输出 {_SILENT}，不要写任何正文。\n"
            "3. 控制符不会发给群友；正文里也不要再出现它。\n"
            "4. 正文一两句话，口语化，像真人随手打字；不用礼貌套话，不主动总结或收尾。"
        )

    def _messages(self, persona: dict, trigger: str, recent_messages: list[dict]) -> list[dict]:
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt(persona, trigger)}
        ]
        for m in recent_messages[-30:]:
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            role = "assistant" if m.get("sender_type") == "ai" else "user"
            messages.append({"role": role, "content": text})
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
        decision: str | None = None       # None / "speak" / "silent"
        head = ""                          # 未判定前缓冲的开头
        collected: list[str] = []          # 已作为 delta 发出的正文

        try:
            async for piece in self._stream(messages, budget):
                if decision is None:
                    head += piece
                    probe = head.lstrip()
                    upper = probe.upper()
                    if upper.startswith(_SPEAK) or upper.startswith(_SILENT) or (
                        _SPEAK.startswith(upper) or _SILENT.startswith(upper)
                    ):
                        if upper.startswith(_SPEAK):
                            decision = "speak"
                            rest = probe[len(_SPEAK):].lstrip()
                            head = ""
                            yield {"gate": "speak"}
                            if rest:
                                collected.append(rest)
                                yield {"delta": rest}
                        elif upper.startswith(_SILENT):
                            decision = "silent"
                            head = ""
                            yield {"gate": "silent"}
                            # [SILENT] 后即便模型又写了内容也不下发
                        else:
                            # 控制符被切断（如 "[" / "[SP"），继续缓冲
                            if len(probe) >= _MAX_TOKEN_PREFIX:
                                decision = "speak"
                                text, head = head, ""
                                yield {"gate": "speak"}
                                collected.append(text)
                                yield {"delta": text}
                            continue
                    else:
                        # 模型没按协议输出控制符：按开口处理，全文下发
                        decision = "speak"
                        text, head = head, ""
                        yield {"gate": "speak"}
                        collected.append(text)
                        yield {"delta": text}
                elif decision == "speak":
                    collected.append(piece)
                    yield {"delta": piece}
                # decision == "silent" 时丢弃后续内容
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("DeepSeek 调用失败，降级为已读不回：%s: %s", type(exc).__name__, exc)
            if decision == "speak":
                text = "".join(collected).strip()
                yield {"done": TurnDone(
                    text=text, should_speak=bool(text),
                    reason=f"deepseek_partial:{type(exc).__name__}",
                )}
            else:
                if decision is None:
                    yield {"gate": "silent"}
                yield {"done": TurnDone(
                    text="", should_speak=False,
                    reason=f"deepseek_error:{type(exc).__name__}",
                )}
            return

        if decision == "speak":
            text = "".join(collected).strip()
            yield {"done": TurnDone(
                text=text, should_speak=bool(text),
                reason=f"deepseek:spoke(trigger={trigger})",
            )}
        else:
            if decision is None:  # 空流也视为已读不回
                yield {"gate": "silent"}
            yield {"done": TurnDone(
                text="", should_speak=False,
                reason="deepseek:silent(protocol=[SILENT])" if decision == "silent"
                       else "deepseek:silent(empty_stream)",
            )}
