from pydantic import BaseModel


class Message(BaseModel):
    id: int
    room_id: str = "main"
    sender_type: str  # 'human' | 'ai'
    sender_id: str | None = None
    nickname: str
    avatar: str | None = None
    text: str
    created_at: int
    # ---- 语音（默认 none；pending 表示正在合成，ready 才能播，failed 已降级为文字）----
    audio_status: str = "none"
    audio_duration_ms: int = 0
    audio_transcript: str | None = None
