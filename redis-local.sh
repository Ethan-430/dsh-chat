#!/usr/bin/env bash
# DSH-CHAT 本机 Redis 启停（Windows 便携版：无需管理员、不注册系统服务、不改注册表）
# 用法: bash redis-local.sh start | stop | status | run
#   start  普通终端用：后台起进程
#   run    前台运行，供 WorkBuddy 后台任务 / 进程管理器托管（非交互 shell 请用这个）
# 密码只从项目根 .env 读取，不写进配置文件、不出现在命令行。
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$ROOT/.env"
CONF="$ROOT/tools/redis/redis.dsh.conf"
SERVER="$ROOT/tools/redis/redis-server.exe"
CLI="$ROOT/tools/redis/redis-cli.exe"
PIDFILE="$ROOT/data/redis/redis.pid"

if [ ! -f "$ENV_FILE" ]; then
  echo "缺少 $ENV_FILE —— 请复制 .env.example 并用 secrets.token_hex(24) 生成密码" >&2
  exit 1
fi
set -a
. "$ENV_FILE"
set +a
: "${REDIS_PASSWORD:?REDIS_PASSWORD 未设置}"

# 用 REDISCLI_AUTH 传密码，避免 -a 出现在命令行/进程列表里
ping() {
  REDISCLI_AUTH="$REDIS_PASSWORD" "$CLI" -h 127.0.0.1 -p 6379 PING 2>/dev/null | grep -q PONG
}

start() {
  if ping; then
    echo "Redis 已在运行 (127.0.0.1:6379)"
    return 0
  fi
  mkdir -p "$ROOT/data/redis"
  # 重定向输出，避免非交互 shell 等待后台进程的 stdout 管道
  "$SERVER" "$CONF" --requirepass "$REDIS_PASSWORD" >> "$ROOT/data/redis/stdout.log" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 40); do
    if ping; then
      echo "Redis 已启动 (pid $(cat "$PIDFILE"), 127.0.0.1:6379, requirepass + AOF)"
      return 0
    fi
    sleep 0.3
  done
  echo "启动超时，查看日志: $ROOT/data/redis/redis.log" >&2
  return 1
}

stop() {
  REDISCLI_AUTH="$REDIS_PASSWORD" "$CLI" -h 127.0.0.1 -p 6379 SHUTDOWN SAVE 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "Redis 已停止（调度状态已由 AOF/RDB 落盘）"
}

run() {
  mkdir -p "$ROOT/data/redis"
  echo $$ > "$PIDFILE"
  exec "$SERVER" "$CONF" --requirepass "$REDIS_PASSWORD"
}

case "${1:-status}" in
  start) start ;;
  run) run ;;
  stop) stop ;;
  status)
    if ping; then echo "Redis running (127.0.0.1:6379, authenticated)"; else echo "Redis not running"; exit 1; fi
    ;;
  *) echo "用法: bash redis-local.sh start|run|stop|status" >&2; exit 2 ;;
esac
