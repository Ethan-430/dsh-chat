"""D5–D7 发言调度器：便宜的「叫醒」+ 冷却状态机，落 Redis。

主链路（chat_router.message 落库并广播后）：
  on_human_message()  -> 计数 / 识别触发 / 重置 45s 静默定时器
  _request_wake()     -> ai_enabled / 每日上限 / 60s 冷却 / 连续主动上限 / 并发互斥
  _run_turn()         -> adapter.turn() 逐字广播 dsh:delta，done 后落库并广播 dsh:done

核心原则：服务端只叫醒，「该不该说话」由 DSH 自己裁决；
should_speak=false -> 不落库、不广播消息、不动冷却与计数。

所有 AI 参数每次唤醒都从 ai_params 表重新读取（群主在 UI 改完立即生效，不用重启）。
Redis 不可用时调度器整体降级为静默，绝不拖垮人类聊天主链路。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from datetime import datetime
from typing import Any

from . import db, state, tts
from .config import settings
from .dsh import get_adapter
from .dsh.persona import DSH_AVATAR, DSH_NICKNAME, DSH_SENDER_ID, DSH_SENDER_TYPE, build_persona
from .policies import ai_param_defaults
from .redis_client import redis

log = logging.getLogger("dsh.scheduler")

# @DSH 必叫（大小写不敏感；"@DSH · AI" 也能命中）
_MENTION_RE = re.compile(r"@\s*DSH\b", re.IGNORECASE)
_QUESTION_WORDS = (
    "吗", "呢", "什么", "为啥", "为什么", "怎么", "咋", "谁", "哪", "几时",
    "是不是", "有没有", "能不能", "要不要", "难道", "多少", "求解释",
)


def is_question(text: str) -> bool:
    t = (text or "").rstrip()
    return t.endswith(("?", "？")) or any(w in t for w in _QUESTION_WORDS)


class DshScheduler:
    def __init__(
        self,
        sio: Any,
        *,
        adapter: Any = None,
        redis_client: Any = None,
        room_id: str | None = None,
        prefix: str | None = None,
    ) -> None:
        self.sio = sio
        self.adapter = adapter or get_adapter()
        self.redis = redis_client or redis
        self.room = room_id or settings.default_room
        self.prefix = (prefix or settings.dsh_redis_prefix).rstrip(":")
        self._active = False
        self._ready = False
        self._busy = False
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._silence_task: asyncio.Task | None = None
        self._silence_gen = 0
        self.decisions: deque[str] = deque(maxlen=12)

    # ---------- Redis key ----------

    def k(self, name: str) -> str:
        return f"{self.prefix}:{self.room}:{name}"

    @property
    def calls_key(self) -> str:
        return f"{self.prefix}:calls:{datetime.now():%Y-%m-%d}"

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        self._active = True
        try:
            await self.redis.ping()
            self._ready = True
        except Exception as exc:  # Redis 挂了也要能正常聊天
            self._ready = False
            log.warning("Redis 不可用，DSH 调度器降级为静默：%s", exc)
            return
        # 进程级标记不跨重启保留，避免上次崩溃遗留的 inflight / 定时器挡住新一轮叫醒
        try:
            await self.redis.delete(self.k("inflight"), self.k("silence_timer_set"))
        except Exception as exc:
            log.warning("清理调度标记失败：%s", exc)
        log.info("DSH 调度器已启动 adapter=%s room=%s prefix=%s", self.adapter.name, self.room, self.prefix)

    async def stop(self) -> None:
        self._active = False
        for task in (self._silence_task, *list(self._tasks)):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._silence_task, *list(self._tasks)) if t),
            return_exceptions=True,
        )
        self._tasks.clear()
        self._silence_task = None
        close_adapter = getattr(self.adapter, "aclose", None)
        if close_adapter:
            try:
                await close_adapter()
            except Exception:
                pass
        if self._ready:
            try:
                await self.redis.delete(self.k("inflight"), self.k("silence_timer_set"))
            except Exception:
                pass

    # ---------- 参数 / 决策记录 ----------

    async def _params(self) -> dict[str, str]:
        try:
            return await db.get_ai_params()
        except Exception as exc:
            log.warning("读取 ai_params 失败，回落默认值：%s", exc)
            return ai_param_defaults()

    def _decide(self, reason: str) -> None:
        self.decisions.append(f"{datetime.now():%H:%M:%S} {reason}")
        log.info("DSH 唤醒决策: %s", reason)

    async def _voice_allowed(self, text: str) -> bool:
        """语音护栏：短线、不念稿、不连着发。任何一条不满足都退回文字。"""
        if not settings.voice_enabled:
            return False
        if len(text) > settings.voice_max_chars:
            return False
        try:
            recent_voice = await db.recent_ai_voice_count(self.room, settings.voice_gap_turns)
        except Exception as exc:
            log.warning("语音护栏查询失败，放行本次：%s", exc)
            return True
        return recent_voice == 0

    async def status(self) -> dict:
        params = await self._params()
        out: dict = {
            "ready": self._active and self._ready,
            "adapter": self.adapter.name,
            "room": self.room,
            "busy": self._busy,
            "ai_enabled": params.get("ai_enabled") == "1",
            "daily_call_limit": int(params.get("daily_call_limit", 200)),
            "recent_decisions": list(self.decisions),
            "last_decision": self.decisions[-1].split(" ", 1)[-1] if self.decisions else "",
        }
        if self._ready:
            try:
                pipe = self.redis.pipeline()
                pipe.get(self.k("last_speak_ts"))
                pipe.get(self.k("consecutive_init"))
                pipe.get(self.k("since_dsh_count"))
                pipe.get(self.calls_key)
                last, cons, since, calls = await pipe.execute()
                out.update({
                    "last_speak_ts": int(float(last or 0)),
                    "consecutive_init": int(cons or 0),
                    "since_dsh_count": int(since or 0),
                    "calls_today": int(calls or 0),
                })
            except Exception as exc:
                out["redis_error"] = str(exc)
        return out

    # ---------- 入站人类消息 ----------

    async def on_human_message(self, msg: dict) -> None:
        """chat_router 在消息落库 + 广播后调用；只做便宜判断，实际推理交给后台任务。"""
        if not self._active or not self._ready:
            return
        try:
            params = await self._params()
            if params.get("ai_enabled") != "1":
                self._decide("disabled:ai_enabled=0")
                return

            await self.redis.incr(self.k("since_dsh_count"))
            count = int(await self.redis.get(self.k("since_dsh_count")) or 0)
            await self._reset_silence_timer(int(params.get("silence_trigger_ms", 45000)))

            text = str(msg.get("text") or "")
            if _MENTION_RE.search(text):
                trigger = "mention"
            elif is_question(text):
                trigger = "question"
            elif count >= int(params.get("ignore_threshold", 5)):
                trigger = "ignored"
            else:
                return
            self._spawn(self._request_wake(trigger))
        except Exception as exc:
            log.warning("入站消息调度失败（不影响聊天）：%s", exc)

    async def _reset_silence_timer(self, delay_ms: int) -> None:
        # 代次校验：新人类消息会作废旧定时器，避免与 Redis 标记 TTL 形成竞态
        self._silence_gen += 1
        gen = self._silence_gen
        if self._silence_task and not self._silence_task.done():
            self._silence_task.cancel()
        # 标记只用于状态可见，TTL 故意比触发时间长（进程崩溃时由 start() 清理）
        await self.redis.set(self.k("silence_timer_set"), "1", px=max(1000, delay_ms) + 60000)
        self._silence_task = asyncio.create_task(self._silence_after(delay_ms / 1000, gen))

    async def _silence_after(self, delay: float, gen: int) -> None:
        try:
            await asyncio.sleep(delay)
            if not self._active or gen != self._silence_gen:
                return
            # 房间没人在线时不自言自语，避免空白房间被后台刷屏
            if not state.SESSIONS:
                return
            await self.redis.delete(self.k("silence_timer_set"))
            self._spawn(self._request_wake("silence"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("静默定时器异常：%s", exc)

    def _spawn(self, coro) -> None:
        async def guarded():
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("调度任务异常")

        task = asyncio.create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------- 叫醒闸门 ----------

    async def _request_wake(self, trigger: str) -> None:
        try:
            params = await self._params()
            if params.get("ai_enabled") != "1":
                self._decide("disabled:ai_enabled=0")
                return

            limit = int(params.get("daily_call_limit", 200))
            used = int(await self.redis.get(self.calls_key) or 0)
            if used >= limit:
                self._decide(f"daily_limit:{trigger}")
                return

            # @ 绕过冷却闸与话痨闸（规格第四节「被 @ 必叫」）
            if trigger != "mention":
                last = int(float(await self.redis.get(self.k("last_speak_ts")) or 0))
                cooldown = int(params.get("cooldown_ms", 60000))
                if int(time.time() * 1000) - last < cooldown:
                    self._decide(f"cooldown:{trigger}")
                    return
                cons = int(await self.redis.get(self.k("consecutive_init")) or 0)
                if cons >= int(params.get("consecutive_limit", 2)):
                    self._decide(f"consecutive:{trigger}")
                    return

            async with self._lock:
                if self._busy:
                    self._decide(f"busy:{trigger}")
                    return
                self._busy = True

            token = uuid.uuid4().hex
            try:
                ok = await self.redis.set(
                    self.k("inflight"), token,
                    nx=True, px=int(params.get("max_latency_ms", 8000)) + 2000,
                )
                if not ok:
                    self._decide(f"inflight:{trigger}")
                    return

                # 计数在真正调用 adapter 之前 +1；首次写入设置 24h 过期
                n = int(await self.redis.incr(self.calls_key))
                if n == 1:
                    await self.redis.expire(self.calls_key, 86400)
                if n > limit:
                    await self.redis.decr(self.calls_key)
                    self._decide(f"daily_limit:{trigger}")
                    return

                await self._run_turn(trigger, params)
            finally:
                try:
                    if (await self.redis.get(self.k("inflight"))) == token:
                        await self.redis.delete(self.k("inflight"))
                except Exception:
                    pass
                async with self._lock:
                    self._busy = False
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("request_wake 异常 trigger=%s", trigger)

    # ---------- 推理 + 流式广播 + 落库 ----------

    async def _run_turn(self, trigger: str, params: dict[str, str]) -> None:
        # 取最近 N 条（时间正序），不能用 get_history：那个 ASC+LIMIT 拿到的是开群最早的
        # 消息，群聊超过 30 条后 DSH 就永远看不到当前话题了。
        recent = await db.get_recent_history(self.room, limit=settings.dsh_recent_limit)
        recent_messages = [
            {
                "sender_type": m.get("sender_type"),
                "nickname": m.get("nickname"),
                "text": m.get("text"),
                "created_at": m.get("created_at"),
                # 语音消息的 text 为空，适配层要靠这两个字段还原成「[发来一条 N 秒语音]」
                "audio_status": m.get("audio_status"),
                "audio_duration_ms": m.get("audio_duration_ms"),
            }
            for m in recent
        ]
        budget = {
            "max_tokens": int(params.get("max_tokens", 120)),
            "max_latency_ms": int(params.get("max_latency_ms", 8000)),
        }
        persona = build_persona(params)

        await self._emit("dsh:typing", {"active": True, "trigger": trigger})

        async def consume() -> dict | None:
            gate = "speak"  # 未提供 gate 的适配器默认允许转发 delta
            done: dict | None = None
            async for chunk in self.adapter.turn(
                session_id=DSH_SENDER_ID,
                trigger=trigger,
                recent_messages=recent_messages,
                persona=persona,
                budget=budget,
            ):
                if not isinstance(chunk, dict):
                    continue
                if "gate" in chunk:
                    gate = str(chunk["gate"])
                    continue
                if "delta" in chunk and gate not in ("silent", "voice"):
                    await self._emit("dsh:delta", {"text": str(chunk["delta"])})
                    # 统一节流：不依赖适配器自身的节奏，保证客户端能看到「逐字出现」
                    if settings.dsh_delta_interval_ms > 0:
                        await asyncio.sleep(settings.dsh_delta_interval_ms / 1000)
                if "done" in chunk and isinstance(chunk["done"], dict):
                    done = dict(chunk["done"])
            return done

        try:
            timeout = budget["max_latency_ms"] / 1000 + 0.5
            done = await asyncio.wait_for(consume(), timeout=timeout)
        except asyncio.TimeoutError:
            self._decide(f"timeout:{trigger}")
            done = None
        except Exception as exc:
            self._decide(f"adapter_error:{trigger}:{type(exc).__name__}")
            log.exception("适配器调用失败 trigger=%s", trigger)
            done = None

        try:
            if done and bool(done.get("should_speak")):
                text = str(done.get("text") or "").strip()
                if not text:
                    self._decide(f"empty:{trigger}")
                else:
                    # 语音轮：模型用了 [SPEAK:VOICE]，且通过服务端护栏才真的发语音。
                    # 没通过就照常发文字——「降级」比「发不出去」好。
                    wants_voice = bool(done.get("voice"))
                    voice_ok = wants_voice and await self._voice_allowed(text)
                    if wants_voice and not voice_ok:
                        self._decide(f"voice_blocked:{trigger}")
                    msg = await db.insert_message(
                        room_id=self.room,
                        sender_type=DSH_SENDER_TYPE,
                        sender_id=DSH_SENDER_ID,
                        nickname=DSH_NICKNAME,
                        avatar=DSH_AVATAR,
                        text=text,
                        audio_status="pending" if voice_ok else "none",
                    )
                    await self._emit("dsh:done", msg)
                    if voice_ok:
                        self._spawn(tts.request_synthesis(
                            self.sio, message_id=int(msg["id"]), text=text,
                        ))
                    now_ms = int(time.time() * 1000)
                    pipe = self.redis.pipeline()
                    pipe.set(self.k("last_speak_ts"), str(now_ms))
                    pipe.set(self.k("since_dsh_count"), 0)
                    if trigger == "mention":
                        pipe.set(self.k("consecutive_init"), 0)  # 被 @ 清零话痨计数
                    else:
                        pipe.incr(self.k("consecutive_init"))
                    await pipe.execute()
                    self._decide(f"spoke:{trigger}")
            else:
                # 已读不回：不落库、不广播消息、不动冷却/计数
                await self._emit("dsh:silent", {
                    "trigger": trigger,
                    "reason": str((done or {}).get("reason") or "adapter 未返回内容"),
                })
                self._decide(f"silent:{trigger}")
        finally:
            await self._emit("dsh:typing", {"active": False, "trigger": trigger})

    async def _emit(self, event: str, payload: dict) -> None:
        try:
            # 超时保护：任何广播异常/挂起都不能占住 inflight 锁拖死后续唤醒
            await asyncio.wait_for(self.sio.emit(event, payload), timeout=3)
        except Exception:
            log.exception("广播 %s 失败", event)
