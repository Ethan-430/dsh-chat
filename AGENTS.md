# AGENTS.md

面向在 `C:\fishpool`（DSH-CHAT）中工作的 ZCode Agent 的仓库指引。

## 项目概览

DSH-CHAT 是自部署的小型实时群聊，真人与 AI 群友「DSH」（由 DeepSeek 或本地 Mock 驱动）同处一室闲聊。采用单端口部署：FastAPI 同时提供 REST（`/api/*`）、Socket.IO（`/socket.io` 及本机 TTS worker 反向连接的 `/worker` 命名空间）和前端构建产物（`STATIC_DIR`）。

主要目录与文件：
- `backend/app/` — FastAPI + `python-socketio` + `aiosqlite`（无 ORM）+ Redis（`redis.asyncio`）后端
  - `main.py` — ASGI 装配（`socketio.ASGIApp`）、`lifespan`、SPA 兜底路由（REST 路由必须注册在 `/{full_path:path}` 之前）
  - `config.py` — `pydantic-settings` 配置，自动读取根目录 `.env`（环境变量优先级更高）
  - `db.py` — 原生参数化 SQLite DDL、运行期语音列补齐、CRUD、LIKE 搜索、`get_recent_history()`
  - `auth.py`、`security.py`、`policies.py` — 令牌鉴权、scrypt 密码哈希、输入校验、角色权限矩阵、`AI_PARAM_SPEC` 白名单
  - `chat_router.py`、`rest.py`、`events.py`、`state.py` — Socket.IO 聊天事件、REST 接口、进程内在线会话与 worker 映射
  - `scheduler.py` — 基于 Redis 的触发/冷却/并发互斥状态机（每次唤醒从 SQLite 热读 `ai_params`）
  - `dsh/` — `DSHAdapter` 契约（`adapter.py`）、`MockAdapter`、`DeepSeekAdapter`、`persona.py`、工厂 `get_adapter()`（`__init__.py`）
  - `voice.py`、`voice_router.py`、`tts.py`、`worker_router.py` — 人类录音上传/ffmpeg 转码/Range 播放，以及 `/worker` 反向长连接语音合成
- `backend/tests/` — 独立运行的 Python 端到端与探针脚本（非 pytest）
- `frontend/src/` — React 18 + TypeScript（strict）+ Vite 5 单页应用（`api.ts`、`socket.ts`、`types.ts`、`avatars.ts`、`audioBus.ts`、`theme.ts`、`components/`）
- 忽略目录/非源码目录：`deploy-v3/`、`deploy-tests/`、`tools/`、`data/`、`logs/`、`tmp-*` —— 平台专有适配（如 sharecode 注入、进程内 Redis 替换等）**严禁**合入 `backend/` 或 `frontend/src/`。

## 关键文档

改动敏感区域前先阅读：
- `DSH-CHAT-DESIGN.md` — **唯一权威技术文档**（架构、契约、账号权限 §14、部署排障 §9、测试 §14.8）。注意：§14 的账号与角色权限体系覆盖了 `DSH-CHAT-SPEC.md` 中旧的匿名进入描述。
- `DSH-CHAT-SPEC.md` — 已锁定的产品决策与量化验收标准。
- `PERSONA.md` — DSH 接 DeepSeek 的人设、系统提示词与触发提示的唯一可编辑副本，含代码同步清单（`policies.py`、`dsh/persona.py`、`dsh/deepseek_adapter.py`）。
- `CONTRIBUTING.md` — 开发约定与提交前检查清单。

## 构建、类型检查与测试命令

后端（在 `backend/` 目录执行）：
- 语法检查：`python -m py_compile app\*.py app\dsh\*.py`
- 离线适配层测试（37 项，无需 Redis / 网络 / Key）：`python tests/e2e_d8.py`
- 离线上下文回归测试（无需 Redis / 网络 / Key）：`python tests/e2e_context.py`
- 全链路冒烟测试（需本机 Redis 与 `requirements-dev.txt`，自启隔离端口和临时 SQLite）：`python tests/e2e_smoke.py`
- 调度器与 Mock 流式测试（需本机 Redis）：`python tests/e2e_d5.py`
- 真实 DeepSeek 联调与语感探针（需真实 `DEEPSEEK_API_KEY`；改动人设/提示词后**必跑**，确认可沉默场景沉默率约 50%、无助手腔）：`python tests/smoke_real_d10.py`、`python tests/probe_d10.py`
- 本地开发运行：`python -m uvicorn app.main:app --reload --port 8000`（裸进程整体启动用 `bash run-local.sh`，本机 Redis 用 `bash redis-local.sh start|stop|status`）

前端（在 `frontend/` 目录执行）：
- 类型检查：`npx tsc --noEmit`
- 生产构建：`npm run build`（输出 `frontend/dist/`，`e2e_smoke.py` 会校验构建产物，FastAPI 通过 `STATIC_DIR` 托管）
- 开发服务：`npm run dev`（已将 `/api` 与 `/socket.io`（含 WS）代理到 `http://localhost:8000`）

Docker：
- `docker compose up -d --build`（构建上下文为仓库根目录 `.`，Dockerfile 为 `backend/Dockerfile`，多阶段先构前端再打包后端）。

## 架构边界与硬性不变量

- **DSH 适配层隔离**：`scheduler.py`、`chat_router.py` 和前端只依赖 `DSHAdapter`（`backend/app/dsh/adapter.py`）与 `get_adapter()`。新增 harness 实现该契约即可，不要改调度器或前端。
- **`[SILENT]` / `[SPEAK]` / `[SPEAK:VOICE]` 协议**：`should_speak=False` 时严禁产出正文 delta、不落 SQLite、不广播消息、不消耗冷却与计数。任何 DeepSeek API 或网络异常必须降级为静默，绝不拖垮人类聊天主链路。
- **上下文取数与格式约束**：调度器必须调用 `db.get_recent_history()`（`DESC ... LIMIT` 后翻回正序），禁止使用 `get_history()`（`ASC ... LIMIT`）。人类消息渲染为 `昵称：正文`；AI 历史消息使用纯正文不加说话人前缀，且 `DeepSeekAdapter` 会流式剥离模型意外复述的开头标签（`_LEADING_LABELS`）。
- **服务端鉴权与权限复核**：绝不信任前端隐藏按钮或客户端自报身份。所有受保护 REST 路由（`auth.current_user` / `current_session`）和 Socket.IO `message` 事件都必须实时回查 SQLite（账号状态、会话有效性、禁言到期时间），并通过 `policies.py`（`owner > admin > member`）复核角色权限。
- **网关令牌头约定**：`auth.py` 优先读取 `X-DSH-Token`，其次才看 `Authorization: Bearer`（因为分享网关会改写 `Authorization` 头）。保持两者兼容；令牌存 `localStorage` 不进 Cookie（防 CSRF），数据库 `sessions` 仅存 `sha256(token)`。唯一接受 `?token=` 查询参数的接口是 `GET /api/voice/{id}`（兼容 `<audio>` 标签）。
- **原生 SQL 安全**：`db.py` 所有 SQL 必须使用 `?` 参数化占位符；`LIKE` 查询必须转义 `%`、`_`、`\` 并带 `ESCAPE`；`ai_params` 的键严格受 `policies.py` 的 `AI_PARAM_SPEC` 白名单约束；绝不对外返回 `password_hash` 或 `token_hash`（统一经 `db.public_user()` 过滤）。
- **Redis 兼容性与降级**：`redis_client.py` 显式固定 `protocol=2`（RESP2），以同时兼容本机 Windows 便携版 Redis 5.0 与 Docker 的 `redis:7-alpine`。Redis 不可用时 `DshScheduler` 必须整体降级为静默，不影响人类正常收发消息。
- **语音与 TTS 降级**：`voice_router.py` 通过原始 body + `Content-Type` 接收录音上传（刻意不引入 `python-multipart`）。TTS 任务推送给反向连接 `/worker` 的本机 worker（连接与上传分别校验 `dsh_worker_token` / `X-Worker-Token`）。worker 离线、合成超时或文本超过 `voice_max_chars` 时，必须平滑降级为普通文字消息（`audio_status='failed'`）。

## 开发约定与常见坑

- **语言规范**：默认使用中文进行沟通、注释与文档编写；代码变量名、数据库表/列名及 API JSON 字段名使用英文。
- **禁止 Service Worker 缓存**：严禁引入 Service Worker 或 `vite-plugin-pwa`/Workbox 缓存（历史上有缓存中毒导致永久 `"Not Found"` 的事故）。保持 `/api/*`、`index.html`、`manifest.webmanifest` 返回 `Cache-Control: no-store`。
- **头像白名单同步**：`backend/app/policies.py` 的 `ALLOWED_AVATARS` 与 `frontend/src/avatars.ts` 的 `AVATARS` 必须保持完全一致，且不包含保留给 DSH 的 `🤖`。
- **人设同步**：修改 `PERSONA.md` 不会自动生效，需同步写回 `backend/app/policies.py`（`DEFAULT_PERSONA` 与 `LEGACY_PERSONAS`）、`backend/app/dsh/persona.py` 与 `backend/app/dsh/deepseek_adapter.py` 对应常量。
- **发布副本纪律**：`deploy-v3/` 是仅供发布的分叉（进程内 Redis 替换、webroot 静态托管、独立 `main.py`/`.env`）。按 `CONTRIBUTING.md`，平台专有适配只留在那里；但在发布副本上做出的真实后端功能（如 2026-11-27 的特蕾西娅人设、mention 必叫协议、`DSH_PURGE_ON_START` 维护开关）必须回合到 `backend/`，并同步 `PERSONA.md` / `CHANGELOG.md`。
- **密钥与运行数据保护**：绝不提交 `.env`、`*.db`、`tools/` 及 `deploy-*` 目录。`e2e_smoke.py` 与 `e2e_d5.py` 会在测试前后校验 `data/dsh_chat.db` 指纹以确保零污染。
- **单进程内存状态限制**：`LoginLimiter`（`security.py`）和 `state.SESSIONS` / `state.WORKERS` 均为单进程内存态，如未来扩展为多 worker 部署需先迁移至 Redis。
