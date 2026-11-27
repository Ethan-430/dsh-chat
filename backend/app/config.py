from pathlib import Path

from pydantic_settings import BaseSettings

# 项目根 .env（含 REDIS_URL / REDIS_PASSWORD）：无论从哪个 cwd 启动都能读到。
# Docker 部署时由 compose 的 environment 显式注入，优先级更高。
_PROJECT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    app_name: str = "DSH-CHAT"
    host: str = "0.0.0.0"
    port: int = 8000
    sqlite_path: str = "/data/dsh_chat.db"
    # 本机裸进程默认连 127.0.0.1；Docker 内由 REDIS_URL 覆盖为 redis://redis:6379/0
    redis_url: str = "redis://127.0.0.1:6379/0"
    # 由 .env 提供，供 compose 插值与 redis-local.sh 注入 --requirepass；
    # 后端本身只用 redis_url 连接。声明此字段避免 pydantic-settings 的 extra_forbidden 报错。
    redis_password: str = ""
    default_room: str = "main"
    cors_origins: list[str] = ["*"]
    deepseek_api_key: str = ""

    # ---- D8–D9 真实 harness（DeepSeek，OpenAI 兼容流式）----
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_s: float = 20.0      # SDK 层超时；调度器另有 max_latency_ms 闸门
    dsh_adapter: str = "auto"             # auto | mock | deepseek；测试可强制指定

    # ---- D5–D7 调度器 / Mock harness ----
    dsh_redis_prefix: str = "dsh"          # 隔离实例可设独立前缀，避免污染正式 Redis 状态
    dsh_mock_mode: str = "normal"          # normal | always_speak | always_silent（测试用）
    dsh_mock_delay_ms: int = 35            # Mock 逐字流速；测试可设 0
    dsh_delta_interval_ms: int = 25        # 服务端 delta 广播节流：既像打字，也不冲垮长轮询客户端
    dsh_recent_limit: int = 30             # 交给适配层的近期消息条数
    dsh_mock_voice: str = ""               # "1" 时 Mock 以语音轮产出（联调前端/worker 用）

    # ---- 语音（TTS / 录音）----
    voice_enabled: bool = True
    voice_dir: str = ""                    # 空 = SQLite 同目录下的 voice/
    voice_max_chars: int = 80              # 超过这个字数不允许发语音（防念稿）
    voice_max_ms: int = 60_000             # 单条语音时长上限（微信也是 60 秒）
    voice_timeout_s: float = 15.0          # 合成超时 -> 这条改发文字
    voice_gap_turns: int = 3               # 最近 N 条 AI 消息里已有语音则本次禁
    voice_upload_max_bytes: int = 8 * 1024 * 1024
    dsh_worker_token: str = ""                 # 与本机 tts-worker 共享的密钥（不设则禁用 worker）
    ffmpeg_path: str = ""                  # 空 = 依次找 PATH / tools/ffmpeg/bin/ffmpeg.exe

    # ---- 一次性维护：清空测试期数据（默认关闭）----
    # "" 关闭 | messages 只清聊天记录 | all 连账号与会话一起清（回到首注册即群主）
    # 清理完必须把它从 .env 删掉，否则每次重启都会再清一遍。
    dsh_purge_on_start: str = ""

    # ---- 账号与权限（第十四节）----
    session_ttl_hours: int = 24 * 7       # 登录令牌有效期
    max_message_chars: int = 2000         # 单条消息长度上限
    recall_window_ms: int = 120_000       # 普通成员可撤回自己消息的时限（2 分钟）
    login_max_fail: int = 5               # 同一 用户名+IP 的失败次数上限
    login_window_sec: int = 60            # 限流滑动窗口
    mute_max_minutes: int = 1440          # 单次禁言上限（24 小时）
    max_announcement_chars: int = 500     # 群公告长度上限

    class Config:
        env_file = str(_PROJECT_ENV) if _PROJECT_ENV.exists() else ".env"


settings = Settings()
