import redis.asyncio as aioredis

from .config import settings

# 调度状态（D5+ 使用）；仅本机 127.0.0.1 / 容器内网可访问，不暴露公网。
# 显式 RESP2：本机 Windows 便携版是 Redis 5.0（无 HELLO 命令），
# 而 compose 用 redis:7-alpine；固定 protocol=2 让 redis-py 8.x 同时兼容两者。
redis = aioredis.from_url(settings.redis_url, decode_responses=True, protocol=2)
