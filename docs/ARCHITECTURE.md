# 架构说明

本文只讲运行时设计。产品决策见 `../DSH-CHAT-SPEC.md`，逐模块落地记录见 `../DSH-CHAT-DESIGN.md`。

## 1. 进程与职责

```
浏览器 (React SPA)
   │ HTTPS / WebSocket
   ▼
FastAPI (单进程, 单端口)
   ├── REST  /api/*       账号、权限、搜索、导出、AI 参数
   ├── Socket.IO 事件     实时消息、在线状态、DSH 流式输出
   ├── 静态托管           托管 frontend/dist，含 SPA fallback
   ▼
DshScheduler              触发 / 冷却 / 延迟闸门（状态在 Redis）
   ▼
DSHAdapter.turn()         Mock 或 DeepSeek（OpenAI 兼容流式）
   ▼
SQLite                    消息、账号、会话、AI 参数、群公告
```

- **单端口**：Socket.IO、REST 和前端都由 FastAPI 暴露，简化 Tailscale / 反向代理配置。
- **无后台 worker**：调度器随 FastAPI 生命周期启动（`lifespan`），不需要额外进程。
- **SQLite 是唯一真相**：消息与身份都落库；Redis 只保存可丢失的调度状态（冷却时间戳、计数、静默标记），重启只影响「多久后再次叫醒 DSH」。

## 2. 一次消息的完整路径

1. 客户端通过 `message{text}` 发送文本；服务端从 `state.SESSIONS[sid]` 取账号身份，忽略客户端自报的昵称 / 头像 / 角色。
2. `db` 校验账号状态、会话有效性、禁言状态和文本长度后写库，广播 `message:new`。
3. 调度器做**便宜判断**决定是否唤醒 DSH：被 `@`、疑问句、冷场 ≥ 45s、连续被无视等；随后检查冷却、连续主动发言上限、当日调用预算和延迟闸门。
4. `turn()` 以流式返回 delta；服务端 25ms 节流广播 `dsh:typing` / `dsh:delta`，前端显示逐字气泡。
5. `done.should_speak == false` → 发 `dsh:silent`，**不落库、不广播正文**；为 true → 落库为 DSH 消息，广播 `dsh:done`。

## 3. 发言调度状态机

关键词：触发要便宜，冷却要比触发更重要。

| 触发 | 语义 |
| --- | --- |
| `mention` | 直接 `@DSH`，必回 |
| `question` | 有人提问，默认已读不回，只有确实有话说才接 |
| `silence` | 冷场，默认潜水，绝不主持救场 |
| `ignored` | 连续被无视后的叫醒，可自嘲也可继续沉默 |
| `manual` | 群主手动叫它出声，简短回复 |

闸门（Redis 键前缀由 `DSH_REDIS_PREFIX` 控制）：
- 发言后 60s 冷却期内不再叫醒；
- 连续主动发言上限 2 次，之后必须被 `@`；
- `max_latency_ms` 与 `max_tokens` 防止单次调用拖慢群聊或烧钱；
- AI 参数每次调用前从数据库热读，群主改参数无需重启。

状态只依赖 Redis 的 `get / set(nx,px) / incr / decr / expire / delete / pipeline`，不使用 pub/sub，因此发布到无外部 Redis 的环境时可用等价的进程内实现替换 `redis_client.py`（发布副本就是这么做的）。

## 4. DSH 适配层契约

```python
class DSHAdapter(Protocol):
    model: str
    base_url: str

    def turn(
        self,
        session_id: str,
        trigger: str,
        recent_messages: list[Message],
        persona: str,
        budget: Budget,          # max_tokens / max_latency_ms
    ) -> AsyncIterator[dict]:
        # {"delta": "..."} ... {"done": {"text": str,
        #                               "should_speak": bool,
        #                               "reason": str}}
```

- `get_adapter()` 在 `auto` 模式下按 `DEEPSEEK_API_KEY` 是否为空选择 `DeepSeekAdapter` / `MockAdapter`；`DSH_ADAPTER` 可强制覆盖。
- 控制符协议：回复以 `[SPEAK]` 或 `[SILENT]` 开头，`[SILENT]` 后不得有正文；适配层负责解析并只把正文交给调度器。
- API 出错时适配层吞掉异常并降级为沉默（`reason` 带错误前缀），不把错误文本发给群友。真实 API 冒烟脚本会显式标记这种降级，避免「余额不足」被误认为正常沉默。
- 换 harness 只需实现同一接口并修改工厂，调度器与主链路零改动。

### 人设为何这样写

闲聊没有任务函数收敛输出，模型天然滑向「话痨」或「正确的废话」。因此 persona 明确：有职业背景 / 好恶、允许抬杠和「不知道」、禁止助手腔（「你说得对」「总结一下」等）、不主动收尾。

真实联调时发现即便 persona 到位，提示词里每句「你可以接话」仍会被模型理解成「应该开口」，8 个场景全部开口。修正方式是加入强先验：**潜水是常态、发言是例外；只有被 @ 才必回；每句都接是机器人**。修正后可沉默场景沉默率回到约 50%（与 Mock 设计一致），必回场景零回退。

## 5. 身份与安全边界

- 密码用标准库 `hashlib.scrypt` 加盐哈希；令牌 `secrets.token_urlsafe(32)`，数据库只存 `sha256(token)`。
- 令牌不放 Cookie（避免 CSRF），由前端保存在本地存储并显式附加到请求；每个受保护路由都实时回查数据库，改密 / 踢出 / 过期立即生效。
- 鉴权依赖优先读 `X-DSH-Token`，兼容 `Authorization: Bearer`。前者是因为某些分享网关会改写 `Authorization`；两者都缺失时返回 401。
- Socket.IO 的 `connect` 事件不授信，必须 `join{token}` 完成鉴权；若客户端在 `join` 尚未完成时发消息，服务端给 1.5s 宽限，仍未就绪只回 `compose:error`，不踢用户下线。
- 所有用户输入走白名单校验；头像只能取预定义 emoji；搜索使用参数化 SQL 且转义 `%` `_` `\`。

## 6. 前端

- `App.tsx` 维护 `loading → auth | chat` 状态机；启动时先 `GET /api/auth/me` 校验令牌，通过后才连接 Socket.IO（`autoConnect: false`）。
- 401 统一派发 `dsh:unauthorized`；`auth:invalid` 清令牌回登录页。
- 消息归属按账号 ID 判定：自己的靠右，他人靠左；角色徽章、撤回按钮按权限渲染，但服务端仍逐操作复核。
- `sw.js` 是**自毁型**：安装后清空所有 Cache Storage、注销自身并重载客户端，不注册 `fetch` 拦截。历史版本曾把 404 页面缓存成离线壳导致用户永久看到 "Not Found"，因此宁可放弃离线能力。
- 静态入口 HTML 由服务端返回 `Cache-Control: no-store`，避免边缘缓存把旧脚本发给新访客。
