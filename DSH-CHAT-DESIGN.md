# DSH-CHAT 详细设计（基于 DSH-CHAT-SPEC 收敛版）

> 本文档是 `DSH-CHAT-SPEC.md` 的**技术落地设计**。规格中已锁定的产品决策（DSH 为第五平等参与者、无主题闲聊、N≤20、SQLite 持久化、Tailscale 组网、两周最小原型等）不再赘述，本文只补「怎么用当前流行框架实现」。

> **⚠️ 范围变更（2026-11-08，用户明确要求）**：正式引入「账号 + 密码 + 群主 / 管理员 / 成员」权限体系。这**覆盖**了规格第七节 IN 的「极简 Web UI（昵称进入，无密码）」与 OUT 的「真实账号体系、权限与审计」。第七节的匿名身份模型、第八节的进入页、「搜索/导出无鉴权」等旧描述均已作废，**一律以第十四节为准**。

---

## 一、技术栈总览（已与用户逐条收敛）

| 层 | 选型 | 说明 |
| --- | --- | --- |
| 后端语言 | **Python 3.11+** | 方案 B：AI 生态最顺，调 DeepSeek 最省心 |
| Web 框架 | **FastAPI** | 异步、自带 OpenAPI、轻 |
| 实时通信 | **Socket.IO**（客户端 + `python-socketio` 服务端） | 自动重连/心跳/房间，未来多房间零改动 |
| 数据层 | **原生 SQLite**（`aiosqlite`，不用 ORM） | 手写 SQL，N≤20 性能足够 |
| 调度状态 | **Redis** | 冷却时间戳/连续发言计数/静默标记，重启可保留、为多进程留口 |
| 前端框架 | **React + Vite + TypeScript** | 生态最大，与后续 React Native 路线互通 |
| 手机端 | **PWA 优先** | 加 manifest + Service Worker，Tailscale 直连即可「添加到主屏幕」 |
| DSH 适配 | **内部 Python 模块**（`DSHAdapter` 接口 + Mock/DeepSeek 实现） | 同进程最简，契约隔离，换实现主链路零改动 |
| DSH 展示 | **逐字流式** | 服务端把 harness token 流经 Socket.IO 广播，前端显示「DSH 正在输入/逐字出现」 |
| 前端托管 | **FastAPI 同端口托管 `dist/`** | 单端口、单域名，Tailscale 最省事；PWA 同域无跨域 |
| 部署 | **docker-compose 单栈** | backend + redis，前端构建物打进 backend 镜像 |
| 账号与鉴权 | **scrypt 密码哈希 + Bearer 令牌**（会话落 SQLite） | 全部用标准库实现，无新增第三方依赖 |
| 权限模型 | **群主 / 管理员 / 成员**（参考微信、QQ） | 服务端逐操作复核；首个注册者为群主；管理员可为 0 人 |

---

## 二、目录结构

```
dsh-chat/
├── docker-compose.yml            # backend + redis 单栈（构建上下文 = 项目根）
├── .dockerignore                 # 排除 node_modules / dist / *.db / tools / .workbuddy
├── .gitignore / .env.example     # 密钥模板与忽略规则（.env 本身不入库）
├── .env                         # [本机] Redis 密码 / DeepSeek Key，不入库不入镜像
├── redis-local.sh               # [本机] Redis 启停：start | run | stop | status
├── run-local.sh                  # 无 Docker 时的本地裸进程启动入口
├── tools/redis/                 # [本机] Redis 便携二进制 + redis.dsh.conf（不入库）
├── data/dsh_chat.db              # 运行期 SQLite（compose 中为 chat_data 卷）
├── backend/
│   ├── Dockerfile                # 多阶段：先 build 前端，再把 dist 拷入，最后跑 FastAPI
│   ├── requirements.txt          # 运行依赖
│   ├── requirements-dev.txt      # 测试依赖（requests 等，不进镜像）
│   └── app/
│       ├── main.py               # FastAPI + Socket.IO 装配、REST 注册、静态托管
│       ├── config.py             # env 配置（SQLite / Redis / DeepSeek / 调度参数）
│       ├── db.py                 # aiosqlite 建表、读写、LIKE 搜索、全量查询
│       ├── schemas.py            # Message
│       ├── security.py           # [已实现] scrypt 密码哈希、令牌、白名单校验、登录限流
│       ├── policies.py           # [已实现] 角色矩阵、权限判定、AI 参数白名单
│       ├── auth.py               # [已实现] Bearer 鉴权依赖（每次回查 DB）
│       ├── state.py / events.py  # [已实现] 在线会话索引 + 统一广播
│       ├── chat_router.py        # [已实现] Socket.IO：join(token) / message / presence
│       ├── rest.py               # [已实现] REST：认证、成员管理、撤回、公告、AI 参数、搜索/导出
│       ├── search_service.py     # [已实现 D3] LIKE 关键词搜索（通配符转义、has_more）
│       ├── export_service.py     # [已实现 D4] JSON / Markdown 流式导出
│       ├── redis_client.py       # [D5 已接] Redis 客户端（RESP2；ping/set/incr/pipeline）
│       ├── scheduler.py          # [D5–D7 已实现] 触发/冷却状态机（热读 ai_params）
│       └── dsh/                  # [D5–D9 已实现] adapter.py / mock_adapter.py / deepseek_adapter.py / persona.py；get_adapter() 按 Key 切换
└── frontend/
    ├── package.json
    ├── vite.config.ts            # @vitejs/plugin-react + /api、/socket.io 开发代理
    ├── index.html
    ├── public/
    │   ├── manifest.webmanifest  # 手写 PWA 清单（无 Service Worker）
    │   └── icon.svg
    └── src/
        ├── main.tsx
        ├── App.tsx               # 登录态机（loading/auth/chat）→ 聊天室 + 工具栏 + 面板
        ├── api.ts                # [已实现] Bearer 封装、全部 REST、Blob 下载
        ├── socket.ts             # Socket.IO 客户端单例（autoConnect: false，鉴权后再连）
        ├── types.ts              # User / Role / Member / Message / AI 参数等类型
        ├── avatars.ts            # 12 个 emoji 头像 + DSH 专属头像
        ├── index.css
        └── components/
            ├── AuthScreen.tsx    # [已实现] 登录 / 注册（含头像与首个群主提示）
            ├── MembersPanel.tsx  # [已实现] 成员列表、任命/禁言/踢出、公告、改资料/改密/登出
            ├── AiParamsPanel.tsx # [已实现] 群主专属 AI 参数编辑
            ├── MessageList.tsx   # 撤回权限计算
            ├── MessageItem.tsx   # 自己靠右、他人靠左；角色徽章、撤回按钮、msg-${id} 锚点
            ├── Composer.tsx      # 禁言时禁用输入
            ├── OnlineBadge.tsx
            ├── SearchPanel.tsx   # [已实现 D3] 防抖搜索、高亮、点击定位
            ├── ExportMenu.tsx    # [已实现 D4] JSON / MD 下载（fetch + Blob）
            └── DshTyping.tsx     # [D5–D7 已实现]「DSH 正在输入…」+ 逐字草稿气泡
```

---

## 三、消息数据模型（DDL）

```sql
CREATE TABLE messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id     TEXT    NOT NULL DEFAULT 'main',   -- 预留多房间，原型恒为 'main'
  sender_type TEXT    NOT NULL CHECK(sender_type IN ('human','ai')),
  sender_id   TEXT,                               -- 人类为【账号 ID 字符串】，AI 固定 'dsh'
  nickname    TEXT    NOT NULL,                   -- 冗余快照：取自发送时的账号，不信任客户端
  avatar      TEXT,
  text        TEXT    NOT NULL,
  created_at  INTEGER NOT NULL                    -- Unix 毫秒
);
CREATE INDEX idx_messages_room_time ON messages(room_id, created_at);

-- 第十四节新增：账号 / 会话 / AI 参数 / 群资料
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,                 -- 3–32 位 [A-Za-z0-9_]
  password_hash TEXT NOT NULL,                   -- scrypt$N$r$p$salt$dk
  nickname TEXT NOT NULL, avatar TEXT,
  role   TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner','admin','member')),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','kicked')),
  muted_until INTEGER NOT NULL DEFAULT 0,        -- Unix 毫秒；0 = 未禁言
  created_at INTEGER NOT NULL
);
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,                   -- 仅存 sha256(token)
  user_id INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE TABLE ai_params  (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                         updated_at INTEGER NOT NULL, updated_by INTEGER);
CREATE TABLE group_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL,
                         updated_at INTEGER NOT NULL, updated_by INTEGER);
```

设计要点：
- `room_id` 已预留，未来多房间（规格 OUT）仅需前端加房间选择，表/调度器无需重构。
- `sender_id` 从匿名 `client_id` 改为**账号 ID**：重新登录 / 换设备后自己发过的历史消息仍显示在右侧。
- 撤回 = **物理删除** `DELETE FROM messages WHERE id=?`（原型），历史、搜索、导出立即不再包含该条；服务端广播 `message:recalled`。
- 账号 / 会话 / AI 参数 / 群资料详见第十四节。所有表操作均为参数化 SQL。

---

## 四、DSH 适配层契约（内部接口）

把规格的 `POST /dsh/turn → SSE` 收敛为**进程内异步接口**，既保留「主链路零改动」的可插拔性，又免去一次 HTTP 往返：

```python
# app/dsh/adapter.py
from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class DSHAdapter(Protocol):
    async def turn(
        self,
        *,
        session_id: str,
        trigger: str,            # "mention" | "question" | "silence" | "ignored" | "manual"
        recent_messages: list[dict],
        persona: dict,
        budget: dict,            # {"max_tokens": 120, "max_latency_ms": 8000}
    ) -> AsyncIterator[dict]:
        """
        yield {"delta": str}                  # 逐字片段，服务端转发给客户端
        ... 最后 yield {"done": {"text": str, "should_speak": bool, "reason": str}}
        """
        ...
```

- **MockAdapter**：按 `trigger` 返回预设 persona 风格短句，`async for` 逐字 yield；`should_speak` 覆盖「该说/不该说」两类（如 `silence` 触发时 50% 返回 `false`），用于无 DeepSeek 验收全链路。
- **DeepSeekAdapter**：调 DeepSeek `chat/completions` 流式（OpenAI 兼容 SDK），`should_speak` 由系统提示要求模型在首 token 输出 `[SPEAK]`/`[SILENT]` 控制符，服务端解析后剥离；`reason` 取模型一句话自述（可选）。真实 harness 形态（HTTP/CLI/SDK）未来可在此实现内替换，对外契约不变。

---

## 五、发言调度器状态机（基于 Redis）

**每次人类消息入站（`on_message`）：**
1. `since_dsh_count += 1`（`dsh:{room}:since_dsh_count`）。
2. 重置 45s 静默定时器（删除/重设 `silence_timer_set` 标记）。
3. 若消息含 `@DSH` → `request_wake(trigger="mention")`（**绕过冷却**）。
4. 若是疑问句（结尾 `?` 或含疑问词）→ `request_wake(trigger="question")`。
5. 若 `since_dsh_count >= 5` 且期间无人回 DSH → `request_wake(trigger="ignored")`。

**45s 静默定时器到点：**
- `request_wake(trigger="silence")`（每轮只叫一次）。

**`request_wake(trigger)`：**
```
if trigger != "mention":
    if now - last_speak_ts < 60s:            # 冷却闸
        return SILENT
    if consecutive_init >= 2:                 # 话痨闸：连续主动≤2次
        return SILENT
# 通过冷却 → 调用 adapter.turn(...)
async for chunk in adapter.turn(...):
    if "delta" in chunk: broadcast("dsh:delta", chunk["delta"])
    if "done"  in chunk:
        if chunk["done"]["should_speak"]:
            persist + broadcast("dsh:done", full_text)
            last_speak_ts = now; consecutive_init += 1
            if trigger == "mention": consecutive_init = 0   # 被@清零
            since_dsh_count = 0
        # should_speak=false → 不落库、不广播、不动冷却
```

**Redis Key 设计（`room_id` 为命名空间）：**
| Key | 类型 | 说明 |
| --- | --- | --- |
| `dsh:{room}:last_speak_ts` | float | 上次发言时间，用于 60s 冷却 |
| `dsh:{room}:consecutive_init` | int | 连续主动发言次数（≤2） |
| `dsh:{room}:since_dsh_count` | int | DSH 上次发言后无人回它的消息数 |
| `dsh:{room}:silence_timer_set` | bool | 45s 静默定时标记 |
| `dsh:calls:{YYYY-MM-DD}` | int（`EXPIRE 86400`） | 当日 DSH 调用次数，成本闸门 |

> 第一要素「真人不抢话」由冷却闸 + 话痨闸天然保证；DSH 发言后 60s 内、连续 2 次主动后，除非被 `@`，否则静默。

### 5.1 D5–D7 落地要点（2026-11-12 已实现并验证）

- **适配层**：`dsh/adapter.py` 定义 `DSHAdapter` Protocol（`turn(...)` 产出 `gate` / `delta` / `done`）；`mock_adapter.py` 按 trigger 生成人设短句并逐字 yield，`silence` 触发 50% 已读不回；`persona.py` 装配群主可改的人设文本 + 硬规则。`dsh/__init__.py` 的 `get_adapter()` 是唯一替换点，D8–D9 换真实 harness 时主链路零改动。
- **裁决语义**：`done.should_speak=false` → 不落库、不广播消息、不动冷却/计数；Mock 在沉默时先给 `{"gate":"silent"}` 且不产出正文 delta，避免把「已读不回」的内容提前泄露。服务端对每次广播加 3s 超时保护，广播异常不会占住 inflight 锁。
- **参数热读**：每次入站与每次叫醒都重新读 `ai_params`（群主 UI 改完立即生效，无需重启）；`ai_enabled=0` 直接短路。
- **闸门顺序**：每日调用上限 →（非 `@`）60s 冷却 → 连续主动上限 → 单进程 asyncio 锁 + Redis `SET NX PX` inflight 互斥 → 计数 +1 → `adapter.turn`；`@` 绕过冷却与话痨闸，发言后把连续主动计数清零。
- **静默定时器**：每条人类消息重置一次，用自增代次作废旧任务（避免与 Redis 标记 TTL 竞态）；房间无人在线时不叫醒，防止空白房间被后台刷屏。
- **流式节流**：服务端按 `dsh_delta_interval_ms`（默认 25ms）广播 `dsh:delta`，既呈现「逐字出现」，也避免瞬间几十个包冲垮长轮询客户端；整轮超时为 `max_latency_ms + 0.5s`。
- **状态可观测**：`GET /api/ai/status`（需登录）返回适配器、Redis 就绪、当日调用、连续主动计数、最近 12 条唤醒决策；群主「⚙ AI 参数」面板展示同一份快照。
- **可测性**：`DSH_REDIS_PREFIX` 给隔离实例独立命名空间；`DSH_MOCK_MODE=always_speak\|always_silent` 与 `DSH_MOCK_DELAY_MS` 让端到端断言确定；Redis 不可用时调度器整体降级为静默，不拖垮人类聊天。

### 5.2 D8–D9 落地要点（2026-11-17 已实现并验证）

- **DeepSeek 适配层**：`dsh/deepseek_adapter.py` 用 `openai.AsyncOpenAI` 走 DeepSeek 的 OpenAI 兼容 `/chat/completions` 流式接口，对外产出与 Mock 完全一致的 `gate → delta* → done`。默认 `deepseek-chat` / `https://api.deepseek.com/v1`，可由 `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` / `DEEPSEEK_TIMEOUT_S` 覆盖。
- **决策协议**：system prompt 要求模型首 token 输出 `[SPEAK]` 或 `[SILENT]`。服务端用开头状态机判定并剥离控制符——控制符被切成多 token（如 `[SP`+`EAK]`）也能识别；`[SILENT]` 时不产出任何 delta（不泄露「已读不回」正文）；模型不按协议输出时兜底当作开口。
- **降级不崩**：API/网络异常在 gate 前则发 `gate=silent` + `should_speak=false`；开口后中断则保留已产出正文按 `should_speak=true` 落库；空流视为沉默。异常绝不抛给调度器。`aclose()` 在调度器 stop 时关闭 SDK 的 HTTP 客户端，避免连接泄漏。
- **工厂切换**：`dsh/__init__.py` 的 `get_adapter()` 按 `DSH_ADAPTER`（`auto`/`mock`/`deepseek`）选择；`auto` 下有 `DEEPSEEK_API_KEY` 用 DeepSeek，否则 Mock。填 Key 无需改任何主链路代码。
- **persona 调优**：`persona.py` 的 `HARD_RULES` 扩充针对真实 LLM 的「一眼假」惯性约束（助手腔、中立废话、列 1/2/3、emoji 堆砌、卖萌腔）；MASK 阶段不参与组句，仅随人设透传。真实语感靠 D10 真人实测再迭代。
- **假 HTTP 验证**：`backend/tests/e2e_d8.py` 用注入的假 client 覆盖工厂选择、控制符解析/剥离/切分、已读不回零 delta、空流、异常降级、请求参数与消息角色映射、协议契约，**32/32 通过**，无需真实 Key / 网络 / Redis 且不污染正式库。
- **待办**：填入真实 `DEEPSEEK_API_KEY` 后在 D10 做真人联调，按语感继续调 `persona` 与控制参数（温度 0.85 / top_p 0.95 为初值）。

---

## 六、成本闸门（呼应规格第十节）

- 每次 `turn` 的 `budget`：`max_tokens≈120`、`max_latency_ms≈8000`（闲聊短句足够）。
- 每日硬上限：`dsh:calls:{date}` 触达 **200/天** 后，当天 `request_wake` 直接返回 SILENT，把账单风险锁死在原型期。上限与预算均可在 `config.py` 调。
- **2026-11-08 起**：触发/冷却/预算/人设等参数已可由**群主**在 UI 调整并持久化到 `ai_params` 表（第十四节 14.4）；调度器接入时应优先读表，而非只读 `config.py` 常量。

---

## 七、Socket.IO 事件契约（**2026-11-08 起改为账号鉴权**）

| 方向 | 事件 | Payload |
| --- | --- | --- |
| C→S | `join` | `{token}` ← **唯一入房凭证**；服务端回 `joined` + `history` + `presence` + `members:changed` |
| C→S | `message` | `{text}`（昵称/头像/角色一律由服务端查库，客户端自报字段全部忽略） |
| S→C | `joined` | `{session_id: 账号ID字符串, user: User, announcement}`（仅发给自己） |
| S→C | `history` | `{messages: Message[]}`（进入时回填，最近 200 条） |
| S→C | `message:new` | `Message` |
| S→C | `message:recalled` | `{id, by}`（撤回后广播，前端移除该条） |
| S→C | `presence` | `{online_count, online_ids[]}` |
| S→C | `members:changed` | `{}`（角色/资料/封禁变化，前端重新拉取 `/api/members`） |
| S→C | `announcement` / `system:notice` | 公告更新 / 管理动作提示 |
| S→C | `compose:error` | `{message}`（禁言、超长、空内容等拒绝原因） |
| S→C | `auth:invalid` | `{reason}`（令牌失效 / 被踢出，前端清 token 回登录页） |
| S→C | `dsh:typing` | `{active, trigger}`：DSH 正在输入 / 停止 |
| S→C | `dsh:delta` | `{text}`：逐字片段（服务端 25ms 节流） |
| S→C | `dsh:done` | `Message`：裁决发言，已落库的 AI 消息 |
| S→C | `dsh:silent` | `{trigger, reason}`：裁决已读不回，不落库、不动冷却 |

- **身份模型（已改）**：`Authorization: Bearer <token>` + `join{token}`；令牌由服务端 `secrets.token_urlsafe(32)` 签发，DB 只存 `sha256(token)`。连接建立本身不授信，未 `join` 不能收发消息。`sender_id` 用**账号 ID**，重新登录 / 换设备后自己发过的历史消息仍在右侧。详见第十四节。
- **在线状态**：`state.SESSIONS`（sid → 账号）；断线重连后客户端监听 `connect` 自动用本地令牌重新 `join`。
- **头像**：12 个 emoji 白名单（`avatars.ts` + 服务端 `policies.ALLOWED_AVATARS` 双向约束），注册/改资料时上报并写入每条消息。DSH 用专属 🤖，仅头像 + 昵称区分。

### REST 契约（全部已实现；除 `register` / `login` / `status` / `health` 外均需 Bearer）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/auth/status` | 公开 | `{user_count, has_owner}`，登录页提示「首个注册者将成为群主」 |
| POST | `/api/auth/register` | 公开 | 注册；**首个账号自动 owner**，其余 member |
| POST | `/api/auth/login` | 公开 | 登录；同 用户名+IP 5 次失败 / 60s → 429 |
| POST | `/api/auth/logout` | 登录 | 吊销当前会话并断开该连接 |
| GET | `/api/auth/me` | 登录 | 当前账号 + 群公告 |
| POST | `/api/auth/password` | 登录 | 改密；吊销该账号全部旧会话、返回新令牌 |
| PATCH | `/api/me` | 登录 | 改自己的昵称 / 头像 |
| GET | `/api/members` | 登录 | 成员列表、在线 ID、群公告 |
| POST | `/api/admin/role` | **仅群主** | 任命 / 撤消管理员 |
| POST | `/api/admin/mute` | 群主/管理员 | 禁言（`minutes=0` 解禁） |
| POST | `/api/admin/kick` | 群主/管理员 | 踢出（吊销会话 + 断开连接） |
| POST | `/api/admin/restore` | **仅群主** | 恢复被踢成员 |
| PUT | `/api/group/announcement` | 群主/管理员 | 编辑群公告 |
| GET / PUT | `/api/ai/params` | 读：登录 / **写：仅群主** | 白名单参数，返回 `editable` 供前端渲染 |
| GET | `/api/ai/status` | 登录 | [D5–D7] 调度器快照：适配器、Redis、当日调用、冷却计数、最近唤醒决策 |
| DELETE | `/api/messages/{id}` | 按撤回规则 | 撤回消息 |
| GET | `/api/search?q=&limit=` | 登录 | `LIKE` 子串匹配，参数化 + 通配符转义 |
| GET | `/api/export?format=json\|md` | 登录 | 流式下载；前端用 `fetch` + Blob，令牌不进 URL |

- **LIKE 安全**：`%` `_` `\` 转义并配合 `ESCAPE '\'`；仍为参数化查询。
- **导出**：JSON 含元信息 + `created_at_iso`；Markdown 按日期分组、昵称做转义、正文逐行引用块。响应头 `Content-Disposition` / `no-store` / `nosniff`；中间件统一给所有 `/api/*` 加 `no-store`；SW 排除 `/api/*`。
- **注册顺序（关键）**：`include_router(rest_router)` 必须早于 `mount("/", StaticFiles)`，否则 catch-all 静态托管会吞掉 `/api/*`。
- **已知限制**：`room_id` 恒取 `default_room`；登录限流为单进程内存表（多进程部署需换 Redis）；无邮箱/MFA/审计日志（升级点见第十一节）。

---

## 八、前端（React + Vite + TS）

- **入口 / 登录态机**：`App.tsx` 三态 `loading → auth | chat`。启动时若本地有令牌先 `GET /api/auth/me` 校验，通过后才 `socket.connect()`；`socket.ts` 设 `autoConnect: false`。`AuthScreen` 提供登录 / 注册（注册含昵称与 12 个 emoji 头像），并依据 `/api/auth/status` 提示「首个注册者将成为群主」。REST 401 统一派发 `dsh:unauthorized`，`auth:invalid` 统一清 token 回登录页。
- **权限 UI**：header 展示自己的角色徽章；「👥 成员」面板按角色渲染可操作按钮（任命/撤消管理员、禁言/解禁、踢出/恢复、群公告、改资料/改密/登出）；「⚙ AI 参数」按钮**仅群主可见**，面板内也有 `editable` 兜底。服务端仍逐操作复核，前端隐藏只是体验。
- **消息布局（微信/QQ 风格）**：`MessageItem` 按 `sender_id === 我的账号ID` 判定归属——**自己的消息靠右**（`flex-direction: row-reverse`，主色气泡 `#4f46e5` + 白字、右上直角），**他人靠左**（白底描边气泡、左上直角）；每条消息带 38px 圆角头像，他人额外显示昵称与群主/管理员徽章，悬停时按权限显示「撤回」。`MessageList` 在新消息时自动滚到底部；禁言时 `Composer` 禁用输入。
- **消息流**：`MessageList` 订阅 `message:new` / `dsh:delta` / `dsh:done`，DSH 消息经 `DshTyping` 展示「正在输入」并以 `delta` 逐字追加。
- **类型共享**：`types.ts` 定义 `User / Role / Member / Message / AiParamsResponse / SearchResponse` 等，未来抽成 `@dsh/shared` 供前后端与（潜在的）React Native 复用。
- **D3–D4 工具栏**：聊天页 header 右侧为「搜索」「导出」「成员」「AI 参数（仅群主）」。搜索面板输入防抖 280ms 自动查询、切换关键词时 AbortController 取消在途请求、`<mark>` 高亮用纯字符串切分渲染（**不用 `dangerouslySetInnerHTML`**，业务源码无 `innerHTML` / `eval`）；点击结果按 `msg-${id}` 锚点滚动并短暂高亮，若目标不在 `history` 回填范围则 toast 明确提示。导出改为 `fetch` + Bearer 头 + Blob 下载（令牌不出现在 URL / 浏览器历史），文件名取自 `Content-Disposition`。移动端窄屏有独立适配。
- **PWA**：
  - **手写静态 manifest**（`name "DSH Chat"`、`display: standalone`、主题色、图标、`start_url`），不依赖 workbox / vite-plugin-pwa，供应链面更小。
  - **不使用 Service Worker**：历史版本曾用 SW 做缓存，结果把 404 页面缓存成离线壳，用户永久看到 "Not Found" 且不自愈。现已移除 SW 注册与 `public/sw.js`；聊天是实时应用，离线缓存收益低于风险。未来若要重做离线能力，应使用带内容 hash 的版本化缓存、绝不缓存 HTML 与错误响应，并保留一键清除入口。
  - **部署注意**：浏览器安装 PWA 通常要求 HTTPS；Tailscale 下可用 MagicDNS + `ts.net` HTTPS，纯 HTTP tailnet IP 在部分手机浏览器无法「添加到主屏幕」。此项只影响安装提示，不影响 Web 端使用。

---

## 九、部署（docker-compose 单栈）

```yaml
# docker-compose.yml（当前实际配置）
services:
  backend:
    # 构建上下文必须是项目根：Dockerfile 需同时 COPY frontend/ 与 backend/
    build:
      context: .
      dockerfile: backend/Dockerfile
    ports: ["8000:8000"]
    depends_on: [redis]
    environment:
      REDIS_URL: redis://:${REDIS_PASSWORD:-changeme}@redis:6379/0
      SQLITE_PATH: /data/dsh_chat.db
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}   # 留空则默认走 MockAdapter
    volumes: ["chat_data:/data"]
  redis:
    image: redis:7-alpine
    # 仅容器内网可达，不映射公网端口；设 requirepass（见 13.3）
    command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:-changeme}"]
    volumes: ["redis_data:/data"]
volumes:
  chat_data:
  redis_data:
```
- `backend/Dockerfile` 多阶段：阶段一 `node` 构建 `frontend/dist`；阶段二 `python:3.11-slim` 装依赖、把 `dist` 拷入、由 FastAPI 托管。**构建上下文为项目根**，COPY 路径相对根目录；`.dockerignore` 排除 `node_modules` / `dist` / `*.db`。
- 无 Docker 的本地环境用 `run-local.sh` 起裸进程（`PORT=xxxx` 可覆盖端口），前端 `dist` 由 `STATIC_DIR` 指向、SQLite 落在 `data/`。

### 本机 Windows 开发环境（2026-11-08 已配置并验证）

本机 **无 Docker**、**WSL 被安全策略禁用**、`github.com` 网页/Release 直连不通（但 `api.github.com` 可达），因此改用**便携版 Redis** 而非系统安装：

- **二进制**：tporadowski/redis `v5.0.14.1` 的 `Redis-x64-5.0.14.1.zip`，经 GitHub API 下载（12,617,669 字节，与 API 元数据一致），SHA256 `018ea18a35876383cbb5f4cd0258adfc87747cf9d619bce1cf73a2e36f720ccf`。解压到 `tools/redis/`，**不写注册表、不注册系统服务、不需要管理员**。
- **启动**：`bash redis-local.sh start`（普通终端，后台）或 `bash redis-local.sh run`（前台，交受管后台任务/进程管理器）；`status` / `stop` 同脚本。非交互 shell 里 `start` 的 `&` 子进程会被回收，必须用 `run`。
- **密钥**：项目根 `.env` 存随机 `REDIS_PASSWORD`（48 位 hex）与 `REDIS_URL`；`redis-local.sh` 以 `--requirepass` 注入，密码**不写进 conf、不出现在命令行**（cli 用 `REDISCLI_AUTH` 传递）。`.env.example` 为模板。
- **加固**（`tools/redis/redis.dsh.conf`）：`bind 127.0.0.1` + `protected-mode yes`；AOF `everysec` + RDB（满足 Q13「跨重启保留冷却状态」，已实测）；`maxmemory 64mb` / `noeviction`；`rename-command` 禁用 `FLUSHALL` / `FLUSHDB` / `CONFIG`（保留 `SHUTDOWN` 供脚本落盘停服）。仅监听回环，未认证返回 `NOAUTH`。
- **协议兼容**：本机 Redis 5.0 无 `HELLO`（RESP3 是 Redis 6+），而 compose 用 `redis:7-alpine`；`redis_client.py` 显式 `protocol=2`，让 `redis-py 8.x` 同时兼容两边。若省略，连接会报 `unknown command 'HELLO'`。
- **配置读取**：`config.py` 默认 `redis_url` 改为 `redis://127.0.0.1:6379/0`，并从项目根 `.env`（按文件位置解析，不依赖 cwd）加载；Docker 仍由 compose `REDIS_URL` 覆盖为 `redis://:pw@redis:6379/0`。`Settings` 需声明 `redis_password` 字段，否则 `.env` 里的该键会触发 pydantic-settings 的 `extra_forbidden`。
- `.gitignore` 排除 `.env` / `data/` / `tools/`；`.dockerignore` 排除 `tools/`，避免 47 MB 二进制进入构建上下文。
- **D8–D9 已接入**：`requirements.txt` 已含 `openai` SDK，`deepseek_adapter.py` 用 `AsyncOpenAI` 走流式；`DEEPSEEK_API_KEY` 在 `.env` 填入后由 `get_adapter()` 自动切换，未填则降级 Mock（可用 `DSH_ADAPTER` 强制）。
- **账号体系零新增依赖**：密码哈希用标准库 `hashlib.scrypt`、令牌用 `secrets`，`requirements.txt` 不变（减少供应链面）。账号 / 会话 / AI 参数 / 群公告全部落在同一 `data/dsh_chat.db`（compose 的 `chat_data` 卷），备份策略不变；升级前务必备份，因为该库现在含凭据。
- 访问：家庭服务器上 `docker compose up -d`，Tailscale tailnet 内直连 `http://<host>:8000`（或 HTTPS 域名）。可先不强制 TLS（Tailscale 已加密）。
- 开发期：后端 `uvicorn app.main:app --reload`，前端 `vite`（端口代理到 8000），热重载并行。
- **安全加固（必做，详见第十三节）**：本应用仅经 tailnet 内访问，**不启用 Funnel / Serve / Tailscale SSH**；主机 `tailscaled` 必须设 `TS_STATE_DIR` 且启用 Tailnet Lock；Redis 仅容器内网、不暴露公网；密钥走 env/secret，不进镜像；定期备份 SQLite。

### 9.1 升级、备份与恢复

```bash
docker compose up -d --build                  # 升级（先备份数据库）
docker compose stop backend
cp <chat_data 卷路径>/dsh_chat.db ./backup/dsh_chat.$(date +%F).db
docker compose start backend
```

也可在线用 SQLite 官方 `.backup` 命令生成一致性快照；不要在写入过程中直接复制文件。恢复时停服 → 覆盖数据库 → 启动。库内含密码与会话哈希，备份按敏感数据保管。Redis 只保存冷却 / 计数，丢失仅重置计时，无需与数据库做一致性备份。

### 9.2 反向代理 / 分享网关注意事项

1. **鉴权头可能被改写**：某些网关把 `Authorization` 替换成自己的 JWT，导致登录后所有接口 401。后端优先读 `X-DSH-Token`；前端若不在可信域名下，需在 `index.html` 入口拦截 `fetch` / XHR 把令牌镜像到该头。Socket.IO 的 `join{token}` 不受影响。
2. **入口页必须 `no-store`**：`main.py` 的 SPA fallback 对 `index.html` / `sw.js` / `manifest.webmanifest` 返回 `Cache-Control: no-store`，未知非 API 路径回入口页，未知 `/api/*` 仍回 JSON 404，避免纯文本 "Not Found" 顶掉前端路由。
3. **平台专有改造只在副本做**：分享沙箱通常只有单端口、无外部 Redis、会忽略 `dist/` 构建目录，并给子资源请求加 `sharecode` 鉴权。对应做法是进程内状态替换 `redis_client.py`、静态资源另存非构建目录、入口 HTML 注入 code 到资源/fetch/XHR/WebSocket 请求。**这些补丁不进源码仓库**。

### 9.3 故障排查

| 现象 | 排查 |
| --- | --- |
| 页面显示 `Not Found` | 确认 `STATIC_DIR` 指向含 `index.html` 的目录；清浏览器缓存；确认代理未拦截子资源；旧 SW 缓存需清除 |
| 注册后立即掉登录 | 检查代理是否改写 `Authorization`；前端是否镜像到 `X-DSH-Token`；查 `/api/auth/me` 状态码 |
| 能登录但发消息被踢 | 检查 Socket.IO 代理是否支持 WebSocket 升级；`join{token}` 是否早于 `message`（服务端现有 1.5s 宽限） |
| DSH 不说话 | `GET /api/ai/status` 看适配器与冷却；确认 Key 有效且账户有余额；`@DSH` 是必回场景 |
| DSH 每句都回 | 用 `probe_d10.py` 看沉默率；检查提示词中「默认潜水」强先验是否还在 |
| `unknown command 'HELLO'` | Redis 版本与客户端协议不匹配；保持 `protocol=2` |
| API 返回 402 | DeepSeek 余额不足；适配器降级为沉默，不是代码故障 |

---

## 十、两周里程碑 → 模块映射

| 阶段 | 目标 | 对应模块 | 状态 |
| --- | --- | --- | --- |
| D1–D2 | 聊天骨架：4 人实时收发、刷新历史仍在 | `main.py`（FastAPI+Socket.IO 装配）、`chat_router.py`、`db.py`（建表）、`frontend`（当时为昵称进入，现改为账号登录，见第十四节） | 已实现并验证（含微信/QQ 气泡与自选头像） |
| D3–D4 | 关键词搜索 + JSON/MD 导出 | `rest.py`、`search_service.py`（LIKE）、`export_service.py`、`SearchPanel.tsx` / `ExportMenu.tsx` | 已实现并验证（30 项端到端检查通过） |
| 追加 2026-11-08 | **账号密码 + 群主/管理员/成员权限 + AI 参数管控** | `security.py`、`policies.py`、`auth.py`、`state.py`、`events.py`、`rest.py`、`AuthScreen.tsx`、`MembersPanel.tsx`、`AiParamsPanel.tsx` | 已实现并验证（106 项端到端检查全通过），详见第十四节 |
| D5–D7 | DSH 适配层 + mock + 调度器（触发/冷却）跑通 | `scheduler.py`（Redis 状态机）、`dsh/adapter.py` + `mock_adapter.py` + `persona.py`、`DshTyping.tsx`、`/api/ai/status` | **已实现并验证**（2026-11-12：109 项回归 + 60 项专项全通过） |
| D8–D9 | persona 调优；真实 harness 接入 | `dsh/deepseek_adapter.py`、`persona.py`、`dsh/__init__.py`（只换适配层内部实现） | **已实现并验证**（2026-11-17：假 HTTP 32 项专项；2026-11-18 真实 Key 填入后工厂自动切 DeepSeek，真实 API 联调通过并按语感调优提示词，32+109+60 全过） |
| D10 | Tailscale 部署 + 4 人真人实测打分 | `docker-compose.yml`、Tailscale 实测，按规格第六节验收 | 真实 harness 联调已完成（2026-11-18）：@ 必回 100%、可沉默场景约 50% 已读不回、语感口语无助手腔；待 4 人真人部署实测打分 |

---

## 十一、未来升级点（已在设计中预埋）

| 升级项 | 当前状态 | 预埋位置 |
| --- | --- | --- |
| **FTS5 语义/全文搜索** | `db.search_messages` 的参数化 `LIKE`（已转义 `% _ \`） | 建 FTS5 虚拟表 + 同步触发器，仅替换 `db.search_messages` 与 `search_service`，REST 契约不动 |
| **embedding 话题突变触发** | 原型不做 | `scheduler.py` 加余弦相似度分支；`persona`/向量存 Redis |
| **多房间** | 单房间 | `room_id` 已在表 + 调度 Key 命名空间就绪，仅前端加房间选择 |
| **React Native 真原生 App** | PWA 优先 | 复用 `types.ts` 与业务逻辑，新增 RN 壳 |
| **MFA / 邮箱找回 / 审计日志** | 未做 | `users` 加字段 + `audit_log` 表，在登录与管理动作处插钩子 |
| **登录限流跨进程** | 单进程内存滑动窗口 | `security.LoginLimiter` 换成 Redis 滑动窗口（Redis 已就位） |
| **群主转让 / 多房间 ACL** | 未做 | `users.role` 与 `messages.room_id` 组合扩展 |
| **高可用 / 备份 / 监控** | OUT | Redis 已引入，备份可挂 volume；其余按运营需求再加 |

---

## 十二、与验收标准（规格第六节）的对应

- **4 人 + DSH 聊满 15 分钟**：Socket.IO 持久连接 + 历史回填，断线自动重连。
- **DSH 发言 3~8 次**：冷却闸（60s / 连续≤2）+ 每日上限，天然把发言量压在此区间；若实测偏离，调 `config.py` 参数。
- **至少 1 次被接话**：不主动总结/收尾（persona 硬性禁止助手腔），靠触发机制自然插入。
- **无冷场 >2 分钟**：45s 静默触发兜底叫醒。
- **3/4 人觉得像正常人**：persona 质量 + 逐字流式 + 不抢话，是 D8–D9 实测迭代重点（最不可预测的工作量）。

---

## 十三、Tailscale 安全加固（公开漏洞打补丁）

> 来源：Tailscale Security Bulletins、Cyberdione Labs「Tailworm」(2026-10)、Tailscale 官方基线 kb/1196。以下把已知问题映射为对本项目的**具体补丁**。

### 13.1 已确认问题与补丁

| 编号 | 问题 | 对本项目的影响 | 补丁 / 缓解 |
| --- | --- | --- | --- |
| Tailworm (SSH) | Tailscale SSH 的 `-L/-R` 转发竞态 + 缺特权降级 → 已授权设备提权到 root、偷注册密钥（Tailscale 1.104.2 / headscale 0.29.5） | 家庭服务器若开 Tailscale SSH 且允许转发，一个被攻陷的设备可拿 root | **本项目不在 DSH-CHAT 主机启用 Tailscale SSH**；运维走普通 SSH（仅密钥、禁密码）。如必须用，设 per-pair 授权、关闭 forwarding、高危开 check mode，并升级到 ≥1.104.4 |
| Tailworm (headscale DoS) | 自托管 headscale 暴露公网时，未认证者可持续注册尝试致内存耗尽 → crash loop → 全 tailnet 无法 onboard 新设备 | 若自托管控制面且暴露公网，可用性归零 | 本项目用 **Tailscale SaaS 控制面**（不暴露）；若自托管 headscale：绝不直连公网、放反向代理+限速+IP 白名单、容器设内存上限+重启自愈+注册速率限制、开 Device Approval |
| TS-2025-008 | 节点未设 `--statedir`/`--state`/`TS_STATE_DIR` 时，Tailnet Lock 不校验签名，未签名节点可互通 | Docker/容器部署高发，开了 Lock 也形同虚设 | 主机 `tailscaled` **必须设 `TS_STATE_DIR`**；启用 Tailnet Lock；用 `tailscale lock status` 核查 |
| TS-2025-007 | 一次性 auth key 存在 TOCTOU 可被同毫秒复用 | 自动化注册风险 | 用 ephemeral/one-off key 并定期轮换；开 Device Approval 兜底（服务端已于 2025-11-07 修复） |
| TS-2025-003 | 自建多 DERP 节点用非常量时间比较 mesh 密钥 → 侧信道泄露 | 仅自托管多 DERP 时 relevant | 本项目用 Tailscale 托管 DERP；若自托管则更新并轮换 mesh key |
| App Connectors (Conn25) | 实验性连接器可越权触达云元数据 `169.254.169.254` / K8s 服务，绕过 ACL | 本项目无此场景 | **不使用 App Connectors**；若用，egress 过滤阻断 `169.254.169.254` |
| Funnel / Serve 暴露 | Funnel 把服务公开公网；`TS_SERVE_ALLOW_ALL_INTERFACES` 可能把 served 端口暴露到隧道外 | 误开即公网裸奔 | **不启用 Funnel**；仅 tailnet 内访问；如需外网走反向代理 + TLS + 鉴权；绝不设 `TS_SERVE_ALLOW_ALL_INTERFACES` |

### 13.2 官方基线（kb/1196）落地清单

- **及时升级**：保持 Tailscale 客户端为最新、订阅安全公告 RSS；CI/监控中标记低于 ≥1.104.4 的节点。
- **MFA**：IdP 开启多因子（硬件令牌优先）。
- **ACL 最小权限**：编写 ACL，**仅允许特定设备/标签访问 DSH-CHAT 端口（如 8000）**，禁止 all-to-all；用 tags 管设备、groups 管用户。
- **Tailnet Lock + Device Approval**：启用，签名节点才准入。
- **node-key expiry**：开启，定期轮换设备密钥。
- **审计与告警**：开启配置审计日志 + webhook（新设备 / 策略变更 / key 创建时告警）。
- **PWA HTTPS**：手机「添加到主屏幕」需 HTTPS —— 用 Tailscale MagicDNS `*.ts.net` HTTPS 或反向代理 + Let's Encrypt；纯 HTTP IP 部分浏览器无法安装。注意此 HTTPS 仅限 tailnet 内，不借 Funnel 公开。

### 13.3 应用层自身加固（与 Tailscale 正交）

- **Redis 仅容器内网**：compose 中 redis 不映射公网端口、设 `requirepass`、仅 backend 网络可达。
- **本机 Redis 同样加固**：仅 `bind 127.0.0.1`、密码由 `.env` 注入而不落配置文件、AOF 持久化、禁用 `FLUSHALL/FLUSHDB/CONFIG`、内存上限 64 MB；不注册系统服务、不暴露端口（详见第九节）。
- **密钥不入镜像**：`DEEPSEEK_API_KEY` 等走 env / secret；Dockerfile 不 `ENV` 写密钥。
- **数据备份**：SQLite 消息库每日备份（家庭服务器无 SLA，断电即丢），备份与容器卷分离。
- **静态资源与 API 同源**：FastAPI 同端口托管前端，减少暴露面；REST（`/api/*`）与 Socket.IO 共用该端口。
- **最小暴露**：除 8000 外不在主机开多余端口；tailnet 内已加密，可暂不强制应用层 TLS，但 PWA 安装需 HTTPS 时按 13.2 处理。
- **账号与会话（第十四节）**：scrypt 加盐哈希 + `hmac.compare_digest` 常量时间比较；DB 只存 `sha256(token)`；改密 / 踢出立即吊销该账号全部会话；用户名白名单 `[A-Za-z0-9_]{3,32}`；所有 SQL 参数化且 `LIKE` 转义；登录限流 5 次 / 60s；单条消息上限 2000 字符；XSS 由 React 文本渲染 + Markdown 昵称转义兜底；令牌走 `Authorization` 头而非 Cookie，免疫 CSRF；所有 `/api/*` 响应 `no-store`。

---

## 十四、账号体系与权限设计（2026-11-08 追加，优先级高于第七节旧描述）

### 14.1 需求与范围变更

| 要求 | 落地 |
| --- | --- |
| 账号 + 密码保证安全 | 注册 / 登录 / 登出 / 改密；scrypt 加盐哈希 + Bearer 令牌会话 |
| 防 SQL 注入等问题 | 全参数化 SQL + 输入白名单 + LIKE 转义 + XSS/CSRF/越权/撞库一并处理 |
| 人类用户分「群主 / 管理员」 | `users.role ∈ {owner, admin, member}` |
| 群聊发起者默认为群主 | **首个注册账号**在 `BEGIN IMMEDIATE` 事务内被判定为 `owner`（并发下也只有一个） |
| 管理员可缺位、由群主手动指定 | 初始 0 名管理员；仅 `POST /api/admin/role`（owner）可任命/撤消 |
| **仅群主可调整 AI 参数** | `PUT /api/ai/params` 由 `_require_owner` 守卫；`GET` 返回 `editable` 供前端渲染 |
| 其余权限参考微信 / QQ | 见 14.4 权限矩阵 |

> 本节覆盖规格第七节的「无密码」IN 与「真实账号体系、权限与审计」OUT。

### 14.2 认证与会话流程

1. 注册：`validate_username`（`[A-Za-z0-9_]{3,32}`）→ `validate_password`（8–128 位，须含字母和数字）→ `hash_password` → `create_user`（事务内判首个账号）→ 签发令牌。
2. 令牌：`secrets.token_urlsafe(32)` 原文只返回给客户端并存在 `localStorage`；`sessions.token_hash = sha256(token)`，DB 泄露也不能直接冒充。有效期 `SESSION_TTL_HOURS`（默认 7 天）。
3. 请求：REST 用 `Authorization: Bearer`；**若反向代理 / 分享网关会改写该头，改用 `X-DSH-Token`**（可带或不带 `Bearer ` 前缀，`auth.py` 优先读取它，Authorization 仍兼容）；Socket.IO 用 `join{token}`。**令牌不进 Cookie（免疫 CSRF）、不进 URL（导出改 fetch + Blob）**。
4. 校验：每个受保护路由经 `auth.current_user` **实时回查 DB**；`message` 每次都重新读账号状态、会话有效性与禁言，避免角色变更后旧连接仍持旧权限。
5. 失效：登出删当前会话；改密 / 踢出删该账号**全部**会话并断开其所有连接；过期会话自动清理。

### 14.3 权限矩阵（服务端逐操作复核）

| 能力 | 群主 | 管理员 | 普通成员 |
| --- | :---: | :---: | :---: |
| 发言（未被禁言）、搜索、导出 | ✓ | ✓ | ✓ |
| 改自己的昵称 / 头像 / 密码 | ✓ | ✓ | ✓ |
| 撤回自己的消息（2 分钟内） | ✓ | ✓ | ✓ |
| 撤回他人消息 | 任意 | 仅普通成员 | ✗ |
| 禁言 / 解禁 | 管理员、普通成员 | 仅普通成员 | ✗ |
| 踢出成员 | 管理员、普通成员 | 仅普通成员 | ✗ |
| 任命 / 撤消管理员 | ✓ | ✗ | ✗ |
| 恢复被踢成员 | ✓ | ✗ | ✗ |
| 编辑群公告 | ✓ | ✓ | ✗ |
| **调整 AI 参数** | **✓** | ✗ | ✗ |

统一约束：`can_moderate()` 禁止动自己、禁止动同级或更高角色、**群主不可被管理**；所有管理动作都先查库取最新角色，不信任请求体里的角色字段。前端按角色显示 / 隐藏按钮只是体验层。

### 14.4 AI 参数（仅群主）

`ai_params` 表按 key-value 存储，写入前经 `policies.AI_PARAM_SPEC` **严格白名单 + 类型/范围校验**（未知 key 一律 400，不拼进 SQL、不做动态属性）：

| key | 默认 | 范围 |
| --- | --- | --- |
| `ai_enabled` | `1` | 0/1 |
| `silence_trigger_ms` | 45000 | 5000–600000 |
| `cooldown_ms` | 60000 | 1000–3600000 |
| `ignore_threshold` | 5 | 1–50 |
| `consecutive_limit` | 2 | 0–10 |
| `max_tokens` | 120 | 16–1000 |
| `max_latency_ms` | 8000 | 1000–60000 |
| `daily_call_limit` | 200 | 1–10000 |
| `persona` | 规格第五节人设草案 | ≤2000 字符 |

D5–D7 调度器与 Mock 适配层**已接入**：每次入站 / 每次叫醒都重新读表，群主在 UI 保存后无需重启、立即生效；D8–D9 只需替换 `dsh/__init__.py` 的适配器工厂。`GET /api/ai/status` 与参数面板顶部展示同一份运行快照。

### 14.5 安全设计（防注入等）

| 风险 | 措施 |
| --- | --- |
| **SQL 注入** | 全部 SQL 用 `?` 占位符；用户名白名单正则；搜索 `LIKE` 对 `% _ \` 转义 + `ESCAPE`；排序字段、AI 参数 key 均来自服务端常量，无动态 SQL 拼接 |
| **口令破解** | `hashlib.scrypt`（N=16384, r=8, p=1，内存硬）+ 每账号 16 字节随机盐；`hmac.compare_digest` 常量时间比较；用户不存在时跑等价开销的 dummy verify 抹平时序差 |
| **撞库 / 爆破** | `LoginLimiter` 按 `用户名+IP` 滑动窗口，5 次失败 / 60s → 429 + `Retry-After`；统一「用户名或密码错误」文案不泄露账号是否存在 |
| **会话窃取** | DB 只存令牌哈希；改密 / 踢出立即全量吊销；REST 每次回查；Socket.IO 消息前复核会话 |
| **越权** | 所有管理接口在服务端用 `policies` 复核（14.3），不依赖前端隐藏 |
| **身份伪造** | `message` 的昵称 / 头像 / `sender_id` 全部取自数据库，客户端自报字段忽略 |
| **XSS** | React 文本渲染；搜索高亮纯字符串切分；业务源码无 `innerHTML` / `dangerouslySetInnerHTML` / `eval`；Markdown 导出转义昵称 |
| **CSRF** | 令牌走 `Authorization` 头，不放 Cookie；同源同端口托管 |
| **缓存泄露** | 中间件给所有 `/api/*` 加 `Cache-Control: no-store`；入口 `index.html` / `sw.js` / `manifest.webmanifest` 同样 `no-store`，避免边缘缓存把旧脚本或错误页发给新访客；不使用 Service Worker 缓存 |
| **输入滥用** | 昵称 1–32、消息 ≤2000、公告 ≤500、禁言 ≤1440 分钟、头像 emoji 白名单；拒绝控制字符 |

### 14.6 前端交互

`AuthScreen`（登录/注册 + 头像）→ `App.tsx` 三态登录机 → 聊天页 header 显示角色徽章、`📢 群公告` 条、`👥 成员` 面板（含任命/禁言/踢出/恢复/改资料/改密/登出）、`⚙ AI 参数`（仅群主）、搜索与导出。消息悬停按权限显示「撤回」；禁言时输入框禁用；`auth:invalid` / REST 401 自动回登录页。SW 已升到 `dsh-chat-v4`（D5–D7 版），旧标签页在升版时自动重载一次。

### 14.7 验证（2026-11-08）

独立实例（8014 + 临时 SQLite）**106 项端到端检查全通过**：首个账号为群主、注入式用户名/登录被拒且表完好、弱口令拒绝、令牌篡改 401、5 次失败 429、未登录访问全 401、成员越权全 403、群主任命与 AI 参数白名单/范围校验、管理员边界、Socket.IO 无令牌拒入 + 身份不可伪造 + 禁言拦截、撤回分级、踢出/恢复/改密即时吊销、搜索注入与 `%` 转义、Markdown 昵称转义、DB 仅存 scrypt 哈希与令牌哈希。正式库随后备份并重建为 **0 用户 / 0 消息 / AI 参数默认值 / 空公告**，从空白窗口开始测试。

### 14.8 可复用测试入口（2026-11-08 追加）

```bash
python backend/tests/e2e_smoke.py
```

`backend/tests/e2e_smoke.py` 是**一条命令跑完整回归**的端到端冒烟测试：自启动独立随机端口的 uvicorn 实例 + `tempfile` 临时 SQLite，测完自动关闭进程并清理（含 `close()` 父进程日志句柄以便 Windows 删除）。覆盖 11 组、**109 项断言**：空白状态与鉴权边界、注入/弱口令防护、首个群主、四客户端 Socket.IO 实时与身份不可伪造、角色权限矩阵与禁言、AI 参数白名单、撤回分级、搜索 `%` 转义与导出、踢出/恢复/改密/登出、数据库存储安全、前端构建物。最后额外校验**正式库 `data/dsh_chat.db` 的文件指纹未变**，确保测试零污染。退出码 0 表示全通过。依赖 `requests` + `python-socketio`。

> 断言注意：React DOM 运行时自身的属性白名单字符串里就含 `dangerouslySetInnerHTML`，因此对打包产物做子串判断会误报；XSS 相关检查应针对业务源码（去注释后）。

手动验收可用 `python backend/tests/seed_test_accounts.py` 在正式房间造 **1 个管理员 + 2 个普通成员**（统一测试口令见脚本输出），带 `--clean` 一键清掉这些账号及其消息/会话。脚本只操作用户名白名单、每次写库前自动备份，绝不触碰群主或原有消息；除「任命管理员」需直接改库（不借用群主令牌）外，注册/登录/登出均走正常 HTTP 流程。**空白房间（`has_owner=false`）下脚本会直接拒绝造号并提示先注册群主**，否则首个测试账号会误占 owner、随后又被改成 admin，导致群里没有群主。

```bash
python backend/tests/e2e_d5.py
```

`backend/tests/e2e_d5.py` 是 **D5–D7 专项回归**：先启动本机 Redis（`bash redis-local.sh run`），脚本会起两个隔离实例（随机端口 + 临时 SQLite + 独立 `DSH_REDIS_PREFIX`）。A 用 `always_speak` 验证 @ 必叫、疑问句、被无视 N 条、45s 静默（参数调到 5s 加速）、60s 冷却、连续主动上限、@ 清零、每日调用上限、`ai_enabled` 总开关、参数热更、`dsh:typing/delta/done` 契约、AI 消息进入 history / 搜索 / 导出；B 用 `always_silent` 验证「已读不回」不落库、不广播、不动冷却。**60 项断言**，结束自动删掉自己写下的 Redis 键，并校验正式库指纹未变。`backend/tests/e2e_smoke.py` 同步加了独立前缀 + `always_silent` Mock，保证 **109 项回归**不往测试库插入 AI 消息、不写正式调度状态。

**测试分层与运行入口**：

| 文件 | 检查内容 | 依赖 |
| --- | --- | --- |
| `tests/e2e_d8.py` | 适配器契约、`[SPEAK]/[SILENT]` 协议、提示词断言、零污染（32 项） | 无 Redis / 无网络 |
| `tests/e2e_smoke.py` | 注册→登录→消息→搜索→导出主链路（109 项） | Redis |
| `tests/e2e_d5.py` | 触发 / 冷却 / 计数 / 沉默决策（60 项） | Redis |
| `tests/smoke_real_d10.py` | 真实 DeepSeek 连通性、流式协议、错误降级显式标记 | 有效 Key + 余额 |
| `tests/probe_d10.py` | 多场景开口 / 沉默分布与语感 | 有效 Key + 余额 |

真实 Key 联调时测试进程强制 `DSH_ADAPTER=mock`，避免 `.env` 有 Key 后误调 API。`requests` 仅测试脚本使用，放在 `requirements-dev.txt`，不进运行镜像。

**提示词改动的硬性检查**：真实联调曾出现「8/8 全开口」退化。每次改 persona / 提示词后必须：① `e2e_d8.py` 全过；② `probe_d10.py` 确认 @ 与手动叫出声 100% 开口、可沉默场景沉默率约 50%、无助手腔；③ 三套回归 201 项全过。

**浏览器端到端**：仅用 `curl` 检查 HTTP 200 不足以发现前端脚本未挂载、鉴权头被改写、Socket join 竞态。需要用 Playwright / CDP 驱动真实浏览器走「打开 → 注册 → 进群 → @DSH → 等回复 → 刷新保持登录」，判定点是 `#root` 有实际内容、`/api/auth/me` 为 200、收到 `dsh:done` 或 `dsh:typing`+`dsh:delta`、刷新后历史可见。发布平台专有的 CDP 调试脚本不入库。

### 14.9 已知限制

- 无邮箱 / 手机号 / MFA / 找回密码；账号由用户自行注册，群主通过踢人 + 恢复控制准入。
- 登录限流为**单进程内存表**，多 worker 部署需换 Redis（Redis 已就位）。
- 撤回为物理删除，不保留「XX 撤回了一条消息」的持久化占位。
- 群主不可转让、不可被管理；被踢账号保留用户名（不可重新注册同名），需群主恢复。
- 令牌存 `localStorage`（刷新免登录），若站点遭 XSS 会泄露；已用 React 文本渲染兜底，未来可换 HttpOnly Cookie + CSRF Token。
- 无审计日志；管理动作仅通过 `system:notice` 实时广播。

---

*设计收敛于 2026-11-01，经 grill 流程逐层逼问确认；2026-11-01 追加第十三节 Tailscale 安全加固（基于公开漏洞 Tailworm / TS-2025-00x / kb/1196 打补丁）；2026-11-08 追加第十四节账号体系与权限（依据用户要求覆盖规格第七节的无密码 / OUT 项），并清空房间从空白状态重启；2026-11-12 完成 D5–D7（DSH 适配层 + Mock harness + Redis 调度器 + 逐字流式 + 调度状态可观测）；2026-11-18 完成真实 DeepSeek 联调与语感调优，回合网关鉴权 / join 竞态 / SPA 兜底修复，移除 Service Worker，补齐 9.1–9.3 运维与 14.8 测试分层。201 项回归与真实浏览器端到端全通过。技术栈：Python/FastAPI + Socket.IO + Redis + 原生 SQLite（后端）；React/Vite/TS（前端）；docker-compose 单栈部署，仅 tailnet 内访问、不启用 Funnel/Serve/Tailscale SSH。*
