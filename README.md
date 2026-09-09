# DefectCollab · 研发缺陷协作系统

Multi-user defect collaboration system for R&D teams — **test / product / dev** collaborate on a single, data-driven state machine.

> 测试提 Bug、产品验收到关单、开发修复跟进，三方在统一状态机上协作，数据按团队（工作区）隔离。

## ✨ Features

- **真实账号体系**：注册 / 登录 / JWT，身份由服务端派生，杜绝伪造；密码用 `pbkdf2` 加盐哈希，无明文。
- **工作区（团队）隔离**：不同团队的缺陷数据互不可见，支持邀请码加入团队。
- **三角色 RBAC**：测试 / 产品 / 开发，字段级权限；业务规则抽象为**数据驱动状态机（7 状态 / 7 流转）**，前端按接口元数据动态渲染，改规则不动业务代码。
- **实时协作**：WebSocket 推送 + 前端轮询兜底，多人改动即时可见。
- **可追溯**：状态流转 / 字段变更 / 评论 / 系统事件，均记录人员 + 角色 + 时间。
- **风险兜底**：SLA 超时、重开上限、未指派告警，由 `compute_risk` 实时计算。
- **质量保障**：配套 31 项接口回归测试（鉴权 / 隔离 / 权限 / 全接口 / WebSocket）全绿。

## 🧱 Tech Stack

FastAPI · SQLite · JWT (HS256) · pbkdf2 密码哈希 · WebSocket · 原生 JS 前端（无构建步骤）

## 🚀 Quick Start

```bash
pip install -r requirements.txt
python server.py
# 浏览器打开 http://127.0.0.1:8080
```

- 默认演示账号：`admin` / `admin123`（邀请码 `DEMO`，已进入演示工作区）
- 或点「注册」自建账号（邀请码留空 = 创建自己的团队工作区）

## 🔌 API Overview

| 模块 | 端点 |
|---|---|
| 鉴权 | `POST /api/auth/register` · `POST /api/auth/login` · `GET /api/auth/me` |
| 缺陷 | `GET/POST /api/bugs` · `PATCH /api/bugs/{id}` · `POST /api/bugs/{id}/comment` |
| 看板 | `GET /api/dashboard` |
| 实时 | `WS /ws`（状态变更推送） |

## 🔄 State Machine

状态：`新建 → 处理中 → 已解决 → 待验证 → 已关闭`，含 `重开 / 拒绝 / 阻塞` 等流转，全部由 `state_machine.py` 数据驱动。新增流转只需改状态机定义，无需改业务代码。

## 🧪 Tests

回归测试套件覆盖鉴权、工作区隔离、三角色权限、全部接口与 WebSocket 实时推送（31 项全绿）。

## 📁 Project Layout

```
server.py          # FastAPI 后端：鉴权 / 缺陷 / 看板 / WebSocket
state_machine.py   # 数据驱动状态机（单一事实源）
static/            # 前端：login.html / index.html / app.js / style.css
requirements.txt   # 依赖（fastapi / uvicorn / pyjwt / python-multipart）
start.bat          # 一键启动（Windows）
```

## 📄 License

MIT
