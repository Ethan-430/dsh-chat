# 测试指南

## 测试分层

| 层级 | 文件 | 需要 Redis | 需要真实 Key | 检查内容 |
| --- | --- | --- | --- | --- |
| 契约 / 协议 | `backend/tests/e2e_d8.py` | 否 | 否 | 适配器契约、`[SPEAK]/[SILENT]` 协议、提示词断言、零污染（32 项） |
| 端到端冒烟 | `backend/tests/e2e_smoke.py` | 是 | 否 | 注册 → 登录 → 消息 → 搜索 → 导出等主链路（109 项） |
| 调度器专项 | `backend/tests/e2e_d5.py` | 是 | 否 | 触发 / 冷却 / 计数 / 沉默决策（60 项） |
| 真实 API 冒烟 | `backend/tests/smoke_real_d10.py` | 否 | 是 | DeepSeek 连通性、流式协议、错误降级是否被显式标记 |
| 语感探针 | `backend/tests/probe_d10.py` | 否 | 是 | 多场景开口 / 沉默分布、输出语感 |

所有测试都使用隔离的数据库路径与 Redis 键前缀，不写正式库。

## 运行

```bash
cd backend
python tests/e2e_d8.py        # 32 / 32
python tests/e2e_smoke.py     # 109 / 109（需 Redis）
python tests/e2e_d5.py        # 60 / 60（需 Redis）
```

连接真实 DeepSeek：

```bash
# .env 中填好 DEEPSEEK_API_KEY 且账户有余额
python tests/smoke_real_d10.py
python tests/probe_d10.py
```

测试进程强制使用 Mock 适配器（`DSH_ADAPTER=mock`），即使 `.env` 里有真实 Key 也不会误调 API——注册了 Key 之后这条隔离尤其重要。

## 修改提示词后的必做检查

真实联调中踩过的坑：只改文案措辞就让模型从「50% 沉默」退化成「8/8 全开口」。因此任何 persona / 提示词改动都要：

1. 跑 `e2e_d8.py`，确认控制符协议、提示词关键字断言仍通过；
2. 跑 `probe_d10.py`，确认：
   - 必回场景（`@`、手动叫出声）100% 开口；
   - 可沉默场景沉默率接近 50%（与 Mock 设计一致）；
   - 正文口语化，不出现「你说得对」「总结一下」等助手腔；
3. 跑三套回归，确认 201 项全过。

## 端到端真实浏览器测试

`deploy-tests/` 下曾有一套基于 CDP 驱动无头 Chrome 的脚本，覆盖「打开 → 注册 → 进群 → @DSH → 等回复 → 刷新保持登录」。该目录属于发布平台专有调试产物，未随源码入库；如需同类测试，可用 Playwright / CDP 按以下判定点自行实现：

- 页面 `#root` 内有实际内容（不是空壳，也不是 "Not Found"）；
- 注册后 `/api/auth/me` 返回 200，且页面保持在聊天界面；
- 发出 `@DSH` 后收到 `dsh:done` 或 `dsh:typing` + `dsh:delta`；
- 刷新页面后仍在群内，历史消息可见。

仅用 `curl` 检查 HTTP 200 **不足以**发现前端脚本未挂载、鉴权头被网关改写、Socket 竞态等问题。

## 已知限制

- 登录限流是单进程内存表，多实例部署需要换成 Redis 计数。
- 真实 API 调用会计费；联调脚本只用于验证，不要在生产库上批量跑。
