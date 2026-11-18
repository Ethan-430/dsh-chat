# 部署指南

## 1. 生产部署（Docker Compose）

```bash
cp .env.example .env
# 填写随机 REDIS_PASSWORD；填 DEEPSEEK_API_KEY 启用真实 AI，留空则走 Mock
python -c "import secrets; print(secrets.token_hex(24))"

docker compose up -d --build
docker compose logs -f backend
```

访问 `http://<host>:8000`。数据库落在 `chat_data` 卷（容器内 `/data/dsh_chat.db`），Redis 数据在 `redis_data` 卷。Redis 只接容器网络，未映射宿主端口。

### 升级

```bash
docker compose pull      # 如使用远程镜像
docker compose up -d --build
```

先备份 SQLite（见第 5 节）再升级。

## 2. 无 Docker 的本地 / 家庭服务器

```bash
python -m pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env     # 填写配置
bash run-local.sh        # 绑定 0.0.0.0:8000
```

Redis 需要单独运行：

- 已有系统 Redis：确保监听 `127.0.0.1:6379` 并设 `requirepass`，与 `.env` 的 `REDIS_URL` 一致。
- Windows 便携版：`redis-local.sh` 依赖 `tools/redis/`，该目录含二进制、不入库。可从 Redis 官方或 tporadowski/redis 发行版下载 `redis-server.exe` 与配置文件放到该目录后执行：

```bash
bash redis-local.sh start   # 后台（普通终端）
bash redis-local.sh status
bash redis-local.sh stop
# 非交互环境请用 run（前台）交给进程管理器托管
bash redis-local.sh run
```

本机 Redis 5.0 与 Compose 的 Redis 7 协议差异已在 `redis_client.py` 中通过 `protocol=2` 兼容（避免 `unknown command 'HELLO'`）。

## 3. 网络与安全

推荐 **Tailscale** 组网：应用只在 tailnet 内访问，不开 Funnel / Serve / SSH。

务必确认：

- [ ] Redis 不监听公网，且设置了 `requirepass`；
- [ ] `.env` 不进镜像、不提交 Git（`Dockerfile` / `.dockerignore` 已处理）；
- [ ] SQLite 所在目录权限仅属运行用户；
- [ ] 定期备份数据库；
- [ ] 若经反向代理暴露，代理必须转发 WebSocket 升级与自定义头 `X-DSH-Token`。

### 反向代理 / 分享网关的两个坑

1. **`Authorization` 头可能被网关改写**为它自己的 JWT，导致登录后所有接口 401。后端因此优先读 `X-DSH-Token`（`Authorization` 仍兼容）。如果前端不在可信域名下，需把前端请求里的令牌镜像到 `X-DSH-Token`；`index.html` 可在入口处拦截 `fetch` / `XHR` 自动附加，WebSocket 的 `join{token}` 不受影响。
2. **入口 HTML 必须 `no-store`**，否则边缘缓存可能把旧版脚本或错误页发给新访客。FastAPI 的 SPA fallback 已对 `index.html` / `sw.js` / `manifest.webmanifest` 设置 `Cache-Control: no-store`。

此外静态托管使用显式文件查找 + 兜底返回 `index.html`，未知非 API 路径不会返回纯文本 `Not Found`；未知 `/api/*` 仍返回 JSON 404。

## 4. 配置要点

| 变量 | 生产建议 |
| --- | --- |
| `REDIS_PASSWORD` | 随机 48 位 hex，与 `REDIS_URL` 中一致 |
| `DEEPSEEK_API_KEY` | 通过环境变量 / secret 注入；**不要写进镜像层或 Git** |
| `SQLITE_PATH` | Compose 默认 `/data/dsh_chat.db`，挂载持久卷 |
| `STATIC_DIR` | Docker 镜像内 `/app/static`，裸进程指向 `frontend/dist` |
| `SESSION_TTL_HOURS` | 默认 168 小时；缩短可提高安全性 |

配置解析见 `backend/app/config.py`。`.env` 从项目根按文件位置读取，不依赖启动目录。

## 5. 备份与恢复

SQLite 是单文件，但**不要在写入过程中直接复制**。推荐：

```bash
# 停服后复制
docker compose stop backend
cp /var/lib/docker/volumes/<project>_chat_data/_data/dsh_chat.db ./backup/dsh_chat.$(date +%F).db
docker compose start backend
```

或在线使用 SQLite 官方 `.backup` 命令生成一致性快照。

恢复：停服 → 覆盖数据库文件 → 启动。注意库内含密码哈希与会话哈希，备份文件按敏感数据保管。

Redis 中的冷却状态丢失只会让 DSH 的冷却计时重置，不需要与 SQLite 做一致性备份。

## 6. 故障排查

| 现象 | 排查 |
| --- | --- |
| 打开页面显示 `Not Found` | 确认 `STATIC_DIR` 指向存在 `index.html` 的目录；清浏览器缓存 / 检查旧 Service Worker 是否被清除；确认代理没有把子资源请求拦掉 |
| 注册后立即掉回登录页 | 检查代理是否改写 `Authorization`；确认前端把令牌镜像到 `X-DSH-Token`；查 `/api/auth/me` 的实际状态码 |
| 能登录但发消息被踢 | 检查 Socket.IO 代理是否支持 WebSocket 升级；确认 `join{token}` 在 `message` 前完成（服务端现在有 1.5s 宽限） |
| DSH 一直不说话 | `GET /api/ai/status` 看适配器与冷却状态；确认 Key 有效且账户有余额；`@DSH` 是必回场景 |
| DSH 每句都回 | 用 `tests/probe_d10.py` 看沉默率；检查 `deepseek_adapter.py` 提示词中「默认潜水」的强先验是否还在 |
| `unknown command 'HELLO'` | Redis 版本 ≥ 6 且客户端协议设置不对；保持 `protocol=2` |
| API 返回 402 | DeepSeek 账户余额不足；适配器会降级为沉默，不是代码故障 |

## 7. 发布到分享网关时的专有改造

本项目源码不含任何平台专有的发布补丁。若需要发布到带 `sharecode` 鉴权的分享沙箱，通常需要：

- 沙箱只有单端口、无外部 Redis → 用进程内状态实现替换 `redis_client.py`；
- 沙箱会忽略构建输出目录 → 把前端资源复制到非 `dist` 名称的目录并让静态托管检测；
- 子资源请求不带 `sharecode` → 在入口 HTML 中把 URL 里的 code 注入到资源、fetch、XHR 与 WebSocket 请求；
- 发布实例与本地实例使用不同数据库文件，避免互相污染。

这些改造应放在独立副本中，不要提交到源码仓库。
