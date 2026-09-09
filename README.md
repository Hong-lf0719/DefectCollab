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

状态：`新建 → 已确认 → 修复中 → 待验证 → 已关闭`，含 `重新打开 / 已拒绝 / 阻塞` 等流转（共 7 状态 / 7 流转），全部由 `state_machine.py` 数据驱动。新增流转只需改状态机定义，无需改业务代码。

## 🧪 Tests

回归测试套件覆盖鉴权、工作区隔离、三角色权限、全部接口与 WebSocket 实时推送（31 项全绿）。

## 🚀 Deploy

### 本地（最快）
```bash
pip install -r requirements.txt
python server.py          # 或 Windows 双击 start.bat
# 打开 http://127.0.0.1:8080  （演示账号 admin / admin123，邀请码 DEMO）
```

### Docker（推荐用于长期运行 / 上云）
```bash
# 构建并后台启动，数据持久化在 ./data 与 ./backups
cp .env.example .env && nano .env   # 至少把 DEFECT_JWT_SECRET 改成随机串
docker compose up -d --build
# 访问 http://<服务器IP>:8080
```
- 镜像基于 `python:3.11-slim`，首次启动自动建库并种子 `admin/admin123` + `DEMO` 工作区。
- 数据卷：`./data`（缺陷库）、`./backups`（每日自动备份，保留 7 天）。
- 改端口：`PORT` 环境变量（默认 8080，已映射到容器）。

### 一键部署到 Render（免费层）
1. 在 Render 新建 **Web Service**，关联本 GitHub 仓库 `Hong-lf0719/DefectCollab`。
2. Runtime 选 **Docker**（仓库根目录已有 `Dockerfile`）。
3. 在 Environment 设置 `DEFECT_JWT_SECRET`（随机串）；Render 会自动注入 `PORT`，无需手动填。
4. 部署完成后，Render 分配 `xxx.onrender.com` 公网地址，直接分享给他人即可访问。
> 免费层实例闲置后会休眠，首次访问需冷启动数秒，属正常现象。

### 国内轻量云 / 自建服务器（公网长期运行）
```bash
# 服务器上
git clone <本仓库> && cd <仓库>
pip install -r requirements.txt
# 用 nohup / systemd / supervisor 守护 python server.py（监听 0.0.0.0:8080）
```
- 前置 **nginx** 反代到 8080，并配置域名 + HTTPS（国内服务器需先完成 ICP 备案）。
- 数据库为单文件 SQLite，小团队足够；若预期高并发，后续可迁移至 Postgres（代码层已抽象数据访问）。

## 📁 Project Layout

```
server.py          # FastAPI 后端：鉴权 / 缺陷 / 看板 / WebSocket
state_machine.py   # 数据驱动状态机（单一事实源）
static/            # 前端：login.html / index.html / app.js / style.css
requirements.txt   # 依赖（fastapi / uvicorn[standard] / pyjwt，版本已锁定）
Dockerfile         # 容器镜像（python:3.11-slim）
docker-compose.yml # 一键编排（含数据卷持久化）
.dockerignore      # 构建时排除 .git / 数据库 / 密钥
.env.example       # 环境变量模板（JWT 密钥 / 端口 / 库路径）
start.bat          # 一键启动（Windows 本地）
```

## 📄 License

MIT
