#!/usr/bin/env bash
# DSH-CHAT 本地裸进程启动（无需 Docker）
# 适用：D1–D10 全流程；Redis 由 redis-local.sh 单独启停，本脚本只检测并提示。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$ROOT/data"

# 加载项目根 .env（REDIS_URL / REDIS_PASSWORD / DEEPSEEK_API_KEY）
if [ -f "$ROOT/.env" ]; then
  set -a
  . "$ROOT/.env"
  set +a
fi

export STATIC_DIR="$ROOT/frontend/dist"
export SQLITE_PATH="$ROOT/data/dsh_chat.db"
export PYTHONPATH="$ROOT/backend"

# Redis 检测：D5+ 调度器依赖；D1–D4 阶段不阻塞启动
if [ -x "$ROOT/tools/redis/redis-cli.exe" ] && [ -n "${REDIS_PASSWORD:-}" ]; then
  if REDISCLI_AUTH="$REDIS_PASSWORD" "$ROOT/tools/redis/redis-cli.exe" -h 127.0.0.1 -p 6379 PING 2>/dev/null | grep -q PONG; then
    echo "Redis      -> 127.0.0.1:6379 OK (requirepass + AOF)"
  else
    echo "Redis      -> 未运行；执行 bash $ROOT/redis-local.sh start"
  fi
fi

cd "$ROOT/backend"
echo "DSH-CHAT   ->  http://localhost:${PORT:-8000}  (bind 0.0.0.0, tailnet/局域网可访问)"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
