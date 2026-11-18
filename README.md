# DSH-CHAT

> 自部署的小型实时群聊：4~N 名真人与一个由 DeepSeek 驱动的 AI「DSH」同处一室闲聊。DSH 不是助理、不是主持人，而是第五个平等参与者——平时潜水，偶尔吐槽，被 `@` 时必回。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009485?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## 特性

- **像真人的 AI**：DSH 自己裁决「说不说」，`[SILENT]` 不落库不广播；未配 Key 时用 Mock harness 离线开发
- **实时聊天**：Socket.IO、逐字流式「正在输入」气泡、在线状态、群公告
- **账号与权限**：注册登录、群主/管理员/成员、禁言/踢出/恢复、改密、登录限流
- **消息能力**：SQLite 持久化、关键词搜索与高亮定位、JSON/Markdown 导出、按权限撤回
- **单端口部署**：FastAPI 同时提供 REST、Socket.IO 与前端静态资源；Docker Compose 一键起 backend + Redis

技术栈：Python 3.11+ / FastAPI / python-socketio / aiosqlite / Redis / DeepSeek（OpenAI 兼容流式） · React 18 / Vite 5 / TypeScript。

## 快速开始

### Docker Compose（推荐）

```bash
git clone <your-repo-url> dsh-chat && cd dsh-chat
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(24))"   # 填到 REDIS_PASSWORD
docker compose up -d --build
```

打开 <http://localhost:8000>。首个注册的账号自动成为群主。

### 无 Docker

```bash
python -m pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env          # 填写 REDIS_PASSWORD / REDIS_URL；DEEPSEEK_API_KEY 可留空
bash run-local.sh             # 需本机 Redis，见设计文档 §9
```

开发时前后端可并行：`backend/` 下 `python -m uvicorn app.main:app --reload --port 8000`，`frontend/` 下 `npm run dev`（Vite 已代理 `/api` 与 `/socket.io`）。

## 配置

所有配置来自环境变量或项目根 `.env`，模板见 [`.env.example`](.env.example)，完整字段见 `backend/app/config.py`。

| 变量 | 作用 |
| --- | --- |
| `REDIS_PASSWORD` / `REDIS_URL` | 调度状态存储；Docker 内由 compose 覆盖为容器地址 |
| `DEEPSEEK_API_KEY` | 留空 → Mock；填入 → 自动切换真实 DeepSeek |
| `DSH_ADAPTER` | `auto`（默认）/ `mock` / `deepseek` |
| `SQLITE_PATH` / `STATIC_DIR` | 数据库文件与前端构建物路径 |
| `SESSION_TTL_HOURS` | 登录令牌有效期，默认 168 小时 |

> `.env` 含密钥，**不会入库**；只提交模板。部署在会改写 `Authorization` 的网关后时，后端优先读取 `X-DSH-Token`。

## 验证

```bash
cd backend
python tests/e2e_d8.py        # 32 项，无需 Redis / 网络
python tests/e2e_smoke.py     # 109 项，需 Redis
python tests/e2e_d5.py        # 60 项，需 Redis
```

测试使用隔离数据库与 Redis 前缀，不污染正式库。真实 Key 联调用 `smoke_real_d10.py` 与 `probe_d10.py`。改动提示词前后的检查要求见设计文档 §14.8。

## 文档

| 文档 | 内容 |
| --- | --- |
| [DSH-CHAT-SPEC.md](DSH-CHAT-SPEC.md) | 产品规格、已锁定决策、量化验收标准 |
| [DSH-CHAT-DESIGN.md](DSH-CHAT-DESIGN.md) | **唯一权威技术文档**：架构、数据模型、DSH 契约、调度器、REST/Socket 契约、部署运维、测试、安全与权限 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发约定与提交前检查 |
| [CHANGELOG.md](CHANGELOG.md) | 按时间记录的用户可见变更 |

常用入口：[技术栈与目录 §1–2](DSH-CHAT-DESIGN.md#一技术栈总览已与用户逐条收敛) · [DSH 契约与调度 §4–5](DSH-CHAT-DESIGN.md#四dsh-适配层契约内部接口) · [接口契约 §7](DSH-CHAT-DESIGN.md#七socketio-事件契约2026-11-08-起改为账号鉴权) · [部署与排障 §9](DSH-CHAT-DESIGN.md#九部署docker-compose-单栈) · [测试 §14.8](DSH-CHAT-DESIGN.md#148-可复用测试入口2026-11-08-追加)。

## 安全

- 不提交 `.env`、`*.db`、发布副本；泄露 Key 请立即在 DeepSeek 控制台吊销。
- Redis 只监听内网并设密码；应用只经 Tailscale tailnet 访问，不开 Funnel / Serve。
- 数据库含密码与会话哈希，备份按敏感数据保管；升级前先备份。
- 登录限流为单进程内存表，多 worker 部署需改为 Redis 计数；无邮箱验证 / MFA / 审计日志。

## 许可证

[MIT](LICENSE) — 可自由使用、修改与分发，保留版权声明即可。
