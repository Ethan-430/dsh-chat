# 贡献指南

## 开发前准备

1. `cp .env.example .env`，按需填写 Redis 与 DeepSeek 配置。
2. 后端：`python -m pip install -r backend/requirements.txt`；跑测试另装 `python -m pip install -r backend/requirements-dev.txt`。
3. 前端：`cd frontend && npm ci`（仓库含 `package-lock.json`）。
4. Redis：Docker 方式由 compose 提供；裸进程方式参考 `DSH-CHAT-DESIGN.md` §9。

## 运行

```bash
# 后端
cd backend && python -m uvicorn app.main:app --reload --port 8000

# 前端（另一个终端，/api 与 /socket.io 已代理到 8000）
cd frontend && npm run dev
```

## 提交前检查

```bash
cd backend
python -m py_compile app/*.py app/dsh/*.py
python tests/e2e_d8.py
python tests/e2e_smoke.py     # 需 Redis
python tests/e2e_d5.py        # 需 Redis

cd ../frontend
npm run build
```

改动了提示词 / persona 时，另跑 `tests/probe_d10.py`（需要有效 Key）确认沉默率没有退化，详见 `DSH-CHAT-DESIGN.md` §14.8。

## 约定

- **不提交密钥**：`.env`、`*.db`、`tools/`、发布副本均在 `.gitignore` 中；不要使用 `git add -f` 绕过。
- **不提交运行数据**：聊天记录含用户内容，账号哈希与会话哈希也在同一库里。
- **源码与发布副本分离**：平台专有的 sharecode 注入、进程内 Redis 替换等只放独立副本，不进 `backend/` 或 `frontend/src/`。
- **中文注释与文档**：项目目前以中文为主，保持一致即可；变量名和 API 字段名用英文。
- **权限在服务端复核**：前端隐藏按钮只是体验，任何角色判断都必须在 REST / Socket 事件里再查一次数据库。
- **适配层隔离**：新增 harness 时实现 `DSHAdapter` 契约，不要改调度器或前端。
- **不要引入 Service Worker 缓存**：历史上有缓存中毒导致永久 "Not Found" 的教训，见 `DSH-CHAT-DESIGN.md` §8。

## 提交信息

建议使用简短的前缀，例如：

```
fix(auth): 网关改写 Authorization 时改用 X-DSH-Token
feat(export): 导出增加 ISO 时间戳
docs: 补充部署排障表
```

## 报告问题

请附上：复现步骤、相关接口状态码、`/api/ai/status` 快照（如涉及 DSH）、以及是否处于反向代理 / 分享网关之后。不要在 issue 里粘贴真实密钥或数据库内容。
