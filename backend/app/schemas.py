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
