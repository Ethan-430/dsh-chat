"""DSH 适配层包：主链路只认 DSHAdapter 契约，换实现不改调度器。

get_adapter() 是唯一替换点；D8–D9 接入真实 DeepSeek 后，调度器 / chat_router /
events 零改动。选择逻辑：
  dsh_adapter=deepseek        -> 强制真实 harness
  dsh_adapter=mock            -> 强制 Mock（测试 / 无 Key 降级）
  dsh_adapter=auto（默认）    -> 有 DEEPSEEK_API_KEY 用 DeepSeek，否则 Mock
"""
from __future__ import annotations

from ..config import settings
from .adapter import DSHAdapter, TurnDone
from .mock_adapter import MockAdapter
from .persona import DSH_AVATAR, DSH_NICKNAME, DSH_SENDER_ID, DSH_SENDER_TYPE, build_persona

__all__ = [
    "DSHAdapter", "TurnDone", "MockAdapter", "get_adapter",
    "DSH_AVATAR", "DSH_NICKNAME", "DSH_SENDER_ID", "DSH_SENDER_TYPE", "build_persona",
]


def get_adapter() -> DSHAdapter:
    """按配置选择 harness；真实 Key 缺失时自动降级 Mock，保证全链路可验收。"""
    choice = (settings.dsh_adapter or "auto").strip().lower()
    if choice == "deepseek":
        from .deepseek_adapter import DeepSeekAdapter

        return DeepSeekAdapter(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_s=settings.deepseek_timeout_s,
        )
    if choice == "auto" and settings.deepseek_api_key:
        from .deepseek_adapter import DeepSeekAdapter

        return DeepSeekAdapter(
            settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            timeout_s=settings.deepseek_timeout_s,
        )
    return MockAdapter(
        mode=settings.dsh_mock_mode,
        delay_ms=settings.dsh_mock_delay_ms,
        voice=(settings.dsh_mock_voice or "").strip() in ("1", "true", "yes", "on"),
    )
