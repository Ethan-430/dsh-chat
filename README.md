# DSH-CHAT

> 一个自部署的小型实时群聊：4~N 名真人与一个由 DeepSeek 驱动的 AI「DSH」在同一个房间里闲聊。DSH 不是助理、不是主持人，而是第五个平等参与者——平时潜水，偶尔吐槽，被 `@` 时必回。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61dafb?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009485?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 目录

- [它解决什么](#它解决什么)
- [功能](#功能)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置](#配置)
- [DSH 是怎么工作的](#dsh-是怎么工作的)
- [项目结构](#项目结构)
- [接口概览](#接口概览)
- [测试](#测试)
- [部署](#部署)
- [安全注意事项](#安全注意事项)
- [文档](#文档)
- [许可证](#许可证)

---

## 它解决什么

普通「AI 聊天机器人」的体验是：你说一句，它回一句，永远热情、永远正确、永远像客服。本项目想验证的是另一件事——**让 AI 像一个真人室友一样待在群里**。

因此核心不是「能不能回复」，而是「该不该说话」：

- 没有话题任务来收敛输出，AI 很容易变成话痨或产生「正确的废话」；
- 解法是强 persona + 服务端便宜的触发/冷却 + 让模型自己裁决沉默；
- 「已读不回」是特性，不是 bug。模型每句都接反而会被判定为「一眼假」。

产品决策与验收标准见 [DSH-CHAT-SPEC.md](DSH-CHAT-SPEC.md)，技术落地细节见 [DSH-CHAT-DESIGN.md](DSH-CHAT-DESIGN.md)。

## 功能

**聊天与 AI**
- 单房间实时聊天（Socket.IO），消息逐字流式显示 DSH 的「正在输入…」气泡
- DSH 可插拔适配层：未配置 Key 时用 `MockAdapter` 跑通全链路，配置后自动切换 `DeepSeekAdapter`
- DSH 自己裁决 `should_speak`；沉默时不落库、不广播
- 群主可调 AI 参数（温度、最大 token、延迟闸门等，白名单校验）

**账号与权限**
- 邮箱-free 注册 / 登录，`scrypt` 加盐哈希，服务端不存明文
- 首个注册者自动成为群主；群主 / 管理员 / 成员三级权限，服务端逐操作复核
- 禁言、踢出与恢复、改密、改资料、群公告；登录失败限流（用户名 + IP，5 次 / 60s）

**消息能力**
- SQLite 持久化；关键词搜索（参数化 `LIKE`，转义通配符）并支持高亮定位
- JSON / Markdown 流式导出
- 按权限撤回消息，前端实时同步

**部署体验**
- FastAPI 单端口同时提供 REST、Socket.IO 和前端静态资源
- 手写 PWA manifest；Service Worker 采用「自毁型」设计，只清理历史错误缓存、不做请求拦截
- Docker Compose 一键起 backend + Redis；也支持无 Docker 的裸进程方式

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.11+ · FastAPI · python-socketio · aiosqlite |
| 调度状态 | Redis（冷却时间戳、连续发言计数、静默标记） |
| AI | DeepSeek（OpenAI 兼容流式，`openai` SDK）；Mock harness 可离线开发 |
| 前端 | React 18 · Vite 5 · TypeScript |
| 鉴权 | 标准库 `scrypt` + `secrets`，会话落 SQLite，无新增依赖 |
| 部署 | Docker Compose（backend + redis），或本地裸进程 |

## 快速开始

> 需要：Python 3.11+、Node.js 20+、Redis（Docker 方式不需要本地 Redis）。

### 方式 A：Docker Compose（推荐）

```bash
git clone <your-repo-url> dsh-chat
cd dsh-chat

cp .env.example .env
# 编辑 .env：设置一个随机 REDIS_PASSWORD；DEEPSEEK_API_KEY 可留空（将使用 Mock）
python -c "import secrets; print(secrets.token_hex(24))"

docker compose up -d --build
```

打开 <http://localhost:8000>。首个注册的账号成为群主。

### 方式 B：本地裸进程（无 Docker）

```bash
# 1) Python 依赖
python -m pip install -r backend/requirements.txt

# 2) 前端构建（FastAPI 会托管 frontend/dist）
cd frontend && npm ci && npm run build && cd ..

# 3) 配置
cp .env.example .env
# 填写 REDIS_PASSWORD / REDIS_URL / DEEPSEEK_API_KEY（可选）

# 4) 启动 Redis（本机已有 redis-server 时可直接启动它；端口 6379）
#    本项目 Windows 便携版脚本需要 tools/redis/，该目录不入库，见 docs/DEPLOYMENT.md

# 5) 启动应用
bash run-local.sh
```

开发时可以让 Vite 与 uvicorn 并行运行（`vite.config.ts` 已把 `/api` 和 `/socket.io` 代理到 8000）：

```bash
# 终端 1
cd backend && python -m uvicorn app.main:app --reload --port 8000
# 终端 2
cd frontend && npm run dev
```

## 配置

所有配置都来自环境变量 / 项目根 `.env`。**`.env` 含密钥，已在 `.gitignore` 中排除，只提交模板 `.env.example`。**

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `REDIS_PASSWORD` | 空 | 仅供 `docker-compose.yml` 插值与本机 Redis 脚本使用 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 调度状态存储；Docker 内为 `redis://:<pw>@redis:6379/0` |
| `DEEPSEEK_API_KEY` | 空 | 留空 → 自动使用 Mock；填入 → 自动切换真实 DeepSeek |
| `DSH_ADAPTER` | `auto` | `auto` / `mock` / `deepseek`，用于强制指定 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容端点 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `DEEPSEEK_TIMEOUT_S` | `20` | SDK 超时（秒） |
| `SQLITE_PATH` | `/data/dsh_chat.db` | 聊天 / 账号 / 会话 / AI 参数的数据库路径 |
| `STATIC_DIR` | `static` | 前端构建物目录（Docker 镜像内为 `/app/static`） |
| `SESSION_TTL_HOURS` | `168` | 登录令牌有效期 |
| `DSH_REDIS_PREFIX` | `dsh` | 调度状态键前缀；测试用不同前缀避免互相污染 |
| `DSH_MOCK_MODE` | `normal` | `normal` / `always_speak` / `always_silent` |
| `DSH_MOCK_DELAY_MS` | `35` | Mock 逐字流速；测试可设 `0` |

完整可调项见 `backend/app/config.py`。

## DSH 是怎么工作的

```
真人消息 ──► SQLite 持久化 ──► 广播给所有人
                 │
                 ▼
          发言调度器（便宜判断）
      触发：@ / 疑问句 / 冷场 / 连续被无视
      闸门：冷却时间、连续主动发言上限、延迟预算
                 │ should_wake = true
                 ▼
      DSHAdapter.turn(session_id, trigger, recent, persona, budget)
                 │ 流式 delta
                 ▼
     模型输出 [SPEAK]正文 或 [SILENT]
                 │
        ┌────────┴────────┐
     SPEAK              SILENT
   落库 + 广播        不落库、不广播
```

关键设计：**服务端只决定「叫不叫醒」，不决定「说不说」**。是否发言由模型通过控制符自行裁决；`[SILENT]` 是正常且期望的结果。适配层契约见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#dsh-适配层契约)。

提示词与人设位于 `backend/app/dsh/persona.py` 和 `backend/app/dsh/deepseek_adapter.py`；改动前请阅读 `backend/tests/probe_d10.py` 的多场景探针——真实联调中曾出现「8/8 全部开口」的退化，就是靠它发现并修正的。

## 项目结构

```
dsh-chat/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI + Socket.IO 装配、SPA 静态托管
│   │   ├── config.py          # 环境变量
│   │   ├── db.py              # aiosqlite 建表与读写
│   │   ├── security.py        # scrypt / 令牌 / 输入校验 / 登录限流
│   │   ├── auth.py            # 鉴权依赖（X-DSH-Token 优先，兼容 Bearer）
│   │   ├── policies.py        # 角色矩阵与 AI 参数白名单
│   │   ├── chat_router.py     # Socket.IO 事件
│   │   ├── rest.py            # REST 接口
│   │   ├── scheduler.py       # 触发 / 冷却 / 延迟闸门状态机
│   │   ├── redis_client.py    # Redis 状态客户端
│   │   └── dsh/               # adapter / mock / deepseek / persona
│   ├── tests/                 # 回归与真实 API 探针
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/                   # React 组件、API/Socket 封装、类型
│   ├── public/                # manifest / icon / 自毁型 sw.js
│   └── vite.config.ts
├── docs/                      # 架构 / 部署 / 测试 / 贡献指南
├── DSH-CHAT-SPEC.md           # 产品规格与验收标准
├── DSH-CHAT-DESIGN.md         # 详细设计、里程碑与安全加固记录
├── docker-compose.yml
├── run-local.sh / redis-local.sh
└── .env.example
```

## 接口概览

REST（除注册 / 登录 / 状态 / 健康检查外均需鉴权）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/auth/status` | 成员数 / 是否已有群主 |
| POST | `/api/auth/register` `/api/auth/login` `/api/auth/logout` | 会话生命周期 |
| GET | `/api/auth/me` | 当前账号与公告 |
| GET | `/api/members` | 成员列表与在线状态 |
| POST | `/api/admin/role` `/api/admin/mute` `/api/admin/kick` `/api/admin/restore` | 群管理 |
| PUT | `/api/group/announcement` | 群公告 |
| GET / PUT | `/api/ai/params` | AI 参数（写仅群主） |
| GET | `/api/ai/status` | 调度器快照（适配器、冷却、调用数） |
| DELETE | `/api/messages/{id}` | 撤回 |
| GET | `/api/search?q=` | 消息搜索 |
| GET | `/api/export?format=json\|md` | 导出 |

Socket.IO：`join{token}`、`message{text}` → `joined` / `history` / `message:new` / `presence` / `dsh:typing` / `dsh:delta` / `dsh:done` / `dsh:silent` / `auth:invalid`。完整表见 [DSH-CHAT-DESIGN.md §7](DSH-CHAT-DESIGN.md#七socketio-事件契约2026-11-08-起改为账号鉴权)。

> 鉴权头：标准写法 `Authorization: Bearer <token>`。若部署在会改写该头的反向代理 / 分享网关后，可改用 `X-DSH-Token`（带或不带 `Bearer ` 前缀均可），后端优先读取它。

## 测试

从 `backend/` 目录运行：

```bash
python tests/e2e_d8.py          # 适配层契约（无需 Redis / 网络）      32 项
python tests/e2e_smoke.py       # 端到端冒烟（需 Redis）             109 项
python tests/e2e_d5.py          # 调度器专项（需 Redis）              60 项
```

以上测试使用隔离的 SQLite / Redis 前缀，**不会污染正式库**。

连接真实 DeepSeek 时需要 `.env` 中有有效 Key：

```bash
python tests/smoke_real_d10.py  # 单次真实流式冒烟，检查协议与降级
python tests/probe_d10.py       # 多场景语感探针：统计开口 / 沉默分布
```

改动提示词后务必同时跑 `e2e_d8.py`（schema / 协议断言）与语感探针，确认「必回场景仍回、可沉默场景保持约 50% 沉默」。详见 [docs/TESTING.md](docs/TESTING.md)。

## 部署

生产推荐 Docker Compose + Tailscale 组网，只在 tailnet 内访问，不启用 Funnel / Serve。步骤、备份、反向代理与分享网关注意事项见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 安全注意事项

- **绝不提交 `.env`**：内含 DeepSeek Key 与 Redis 密码。如果不慎泄露，立即在 DeepSeek 控制台吊销 Key。
- 数据库 `data/dsh_chat.db` 含账号哈希与会话哈希，同样不入库；升级或迁移前先备份。
- Redis 只监听内网 / 容器网络并设置 `requirepass`，不要映射到公网。
- 登录限流目前是单进程内存表；多进程部署需改为 Redis 计数。
- 应用无邮箱验证 / MFA / 审计日志；不要直接暴露到公网。
- 前端所有用户输入经过白名单校验，渲染不使用 `innerHTML` / `eval`；搜索与导出均参数化查询。

## 文档

| 文档 | 内容 |
| --- | --- |
| [DSH-CHAT-SPEC.md](DSH-CHAT-SPEC.md) | 产品规格、已锁定决策、量化验收标准 |
| [DSH-CHAT-DESIGN.md](DSH-CHAT-DESIGN.md) | 详细设计、数据模型、事件契约、里程碑状态、安全加固 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 运行时架构、调度状态机、适配层契约 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker / 裸进程部署、备份、反向代理、故障排查 |
| [docs/TESTING.md](docs/TESTING.md) | 测试分层、运行方式、语感调优方法 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发约定与提交前检查 |

## 许可证

[MIT](LICENSE) — 可自由使用、修改与分发，保留版权声明即可。
