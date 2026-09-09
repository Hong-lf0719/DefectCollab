"""研发缺陷协作系统 —— 后端服务（FastAPI + SQLite）。

测试 / 产品 / 开发 三方在统一状态机上协作跟踪 Bug，覆盖：
- 真实账号体系（注册 / 登录 / JWT，身份由服务端派生，杜绝伪造）
- 工作区（团队）隔离：不同团队的缺陷数据互不可见
- 三角色分工与权限控制（state_machine 数据驱动）
- 共同进度（/api/dashboard）
- 实时监控（WebSocket 推送 + 前端轮询兜底）
- 可追溯（history：状态流转 / 字段变更 / 评论 / 系统事件，均含人员+角色+时间）
- 保底机制（SLA 超时、重开上限、未指派告警，由 compute_risk 实时计算）
"""
from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import jwt
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

import state_machine as sm

HERE = Path(__file__).parent
DB_PATH = Path(os.environ.get("DEFECT_DB", str(HERE / "defects.db")))
STATIC = HERE / "static"
BACKUP_DIR = HERE / "backups"
LOGIN_HTML = STATIC / "login.html"

# ── 鉴权配置（生产环境务必通过环境变量覆盖 SECRET）──
JWT_SECRET = os.environ.get("DEFECT_JWT_SECRET", "dev-secret-change-me")
JWT_ALG = "HS256"
JWT_EXPIRE_DAYS = int(os.environ.get("DEFECT_JWT_EXPIRE", "30"))


# ── 密码哈希（标准库 pbkdf2，零额外依赖）──────────────
def _hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000)
    return f"{salt}${dk.hex()}"


def _verify_pw(pw: str, stored: str) -> bool:
    try:
        salt, dk = stored.split("$", 1)
        return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 100_000).hex() == dk
    except Exception:
        return False


def _gen_code(n: int = 6) -> str:
    return secrets.token_hex((n + 1) // 2).upper()[:n]


# ── 数据层 ────────────────────────────────────────────
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse(t: str) -> datetime:
    return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    conn = _connect()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS workspaces (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                code        TEXT NOT NULL UNIQUE,
                owner_id    INTEGER,
                created_at  TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role         TEXT NOT NULL,
                display_name TEXT NOT NULL,
                workspace_id INTEGER NOT NULL,
                created_at   TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS bugs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL DEFAULT 1,
                title       TEXT NOT NULL,
                description TEXT,
                reporter    TEXT NOT NULL,
                priority    TEXT NOT NULL DEFAULT 'P2-中',
                severity    TEXT NOT NULL DEFAULT '一般',
                assignee    TEXT,
                status      TEXT NOT NULL DEFAULT '新建',
                blocked     INTEGER NOT NULL DEFAULT 0,
                blocked_reason TEXT,
                reopen_count INTEGER NOT NULL DEFAULT 0,
                risk_sig    TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )"""
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(bugs)").fetchall()]
        if "workspace_id" not in cols:
            conn.execute("ALTER TABLE bugs ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1")
        if "risk_sig" not in cols:
            conn.execute("ALTER TABLE bugs ADD COLUMN risk_sig TEXT")
        if "project" not in cols:
            conn.execute(f"ALTER TABLE bugs ADD COLUMN project TEXT DEFAULT '{sm.DEFAULT_PROJECT}'")
            conn.execute(f"UPDATE bugs SET project='{sm.DEFAULT_PROJECT}' WHERE project IS NULL OR project=''")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS attachments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_id     INTEGER NOT NULL,
                filename   TEXT NOT NULL,
                mime       TEXT NOT NULL DEFAULT 'image/png',
                size       INTEGER NOT NULL DEFAULT 0,
                data       TEXT NOT NULL,
                by_role    TEXT NOT NULL,
                actor      TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                bug_id     INTEGER NOT NULL,
                kind       TEXT NOT NULL DEFAULT 'status',
                from_status TEXT,
                to_status   TEXT,
                field      TEXT,
                old_val    TEXT,
                new_val    TEXT,
                by_role    TEXT NOT NULL,
                actor      TEXT,
                comment    TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                action     TEXT NOT NULL,
                bug_id     INTEGER,
                bug_title  TEXT,
                detail     TEXT,
                by_role    TEXT NOT NULL,
                actor      TEXT,
                workspace_id INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )"""
        )
        if "workspace_id" not in [r["name"] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()]:
            conn.execute("ALTER TABLE audit_log ADD COLUMN workspace_id INTEGER NOT NULL DEFAULT 1")
        conn.commit()
        _seed(conn)
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    now = _now()
    # 演示工作区（邀请码 DEMO），便于体验多人协作
    demo = conn.execute("SELECT * FROM workspaces WHERE code=?", ("DEMO",)).fetchone()
    if not demo:
        cur = conn.execute(
            "INSERT INTO workspaces (name,code,owner_id,created_at) VALUES (?,?,?,?)",
            ("演示工作区", "DEMO", None, now),
        )
        demo_id = cur.lastrowid
    else:
        demo_id = demo["id"]

    # 内置管理员（首次启动创建）
    admin = conn.execute("SELECT * FROM users WHERE username=?", ("admin",)).fetchone()
    if not admin:
        conn.execute(
            "INSERT INTO users (username,password_hash,role,display_name,workspace_id,created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("admin", _hash_pw("admin123"), sm.ROLE_PRODUCT, "管理员", demo_id, now),
        )

    # 种子缺陷（仅库空时）
    if conn.execute("SELECT COUNT(*) AS c FROM bugs").fetchone()["c"] == 0:
        samples = [
            ("登录页在 Safari 下点击登录无响应", "复现：1.打开登录页 2.输入账号 3.点登录。预期跳转，实际无反应。",
             "测试", "P1-高", "严重", "", sm.S_NEW, 0),
            ("导出报表时中文文件名乱码", "导出 Excel 文件名含中文时变乱码，影响交付。",
             "测试", "P2-中", "一般", "", sm.S_NEW, 0),
            ("偶发：列表接口 500", "高峰期偶发 500，疑似 N+1 查询。已确认有效，等待指派开发。",
             "产品", "P0-紧急", "致命", "", sm.S_CONFIRMED, 0),
        ]
        for title, desc, rep, pri, sev, asg, st, blk in samples:
            cur = conn.execute(
                "INSERT INTO bugs (workspace_id,title,description,reporter,priority,severity,assignee,status,blocked,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (demo_id, title, desc, rep, pri, sev, asg, st, blk, now, now),
            )
            bid = cur.lastrowid
            conn.execute(
                "INSERT INTO history (bug_id,kind,from_status,to_status,by_role,actor,comment,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (bid, "status", None, st, rep, rep, "初始创建", now),
            )
        conn.commit()


# ── 鉴权 ─────────────────────────────────────────────
def _make_token(user: dict) -> str:
    payload = {
        "uid": user["id"], "u": user["username"], "r": user["role"],
        "n": user["display_name"], "w": user["workspace_id"],
        "exp": datetime.utcnow() + timedelta(days=JWT_EXPIRE_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def _decode_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return None


def _token_from_request(request: Request) -> Optional[str]:
    ah = request.headers.get("Authorization", "")
    if ah.startswith("Bearer "):
        return ah[7:].strip()
    return request.cookies.get("token") or None


def get_user(request: Request) -> dict:
    """依赖：解析 JWT，无效则 401。"""
    token = _token_from_request(request)
    u = _decode_token(token)
    if not u:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return u


# ── 可追溯：写入一条 history ─────────────────────────
def _log(conn, bug_id, *, kind="status", frm=None, to=None, field=None,
         old=None, new=None, role, actor, comment=""):
    conn.execute(
        "INSERT INTO history (bug_id,kind,from_status,to_status,field,old_val,new_val,by_role,actor,comment,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (bug_id, kind, frm, to, field, old, new, role, actor or role, comment, _now()),
    )


# ── 保底机制：实时计算风险 ──────────────────────────
def compute_risk(bug: dict) -> list:
    risks = []
    try:
        updated = _parse(bug["updated_at"])
    except Exception:
        return risks
    age_h = (datetime.now() - updated).total_seconds() / 3600.0
    if bug["status"] == sm.S_VERIFYING and age_h > sm.CONFIG["sla_verify_hours"]:
        risks.append(f"验证超时 {age_h:.0f}h（SLA {sm.CONFIG['sla_verify_hours']}h）")
    if bug["status"] in (sm.S_FIXING, sm.S_REOPENED) and age_h > sm.CONFIG["sla_fix_hours"]:
        risks.append(f"修复超时 {age_h:.0f}h（SLA {sm.CONFIG['sla_fix_hours']}h）")
    if bug["reopen_count"] >= sm.CONFIG["reopen_limit"]:
        risks.append(f"反复重开 ×{bug['reopen_count']}（上限 {sm.CONFIG['reopen_limit']}）")
    if bug["status"] == sm.S_CONFIRMED and not bug.get("assignee"):
        risks.append("已确认但未指派开发")
    if bug.get("blocked"):
        risks.append("开发标记阻塞：" + (bug.get("blocked_reason") or "无说明"))
    return risks


def _enrich(conn: sqlite3.Connection, b: dict) -> dict:
    """补充风险列表；保底告警签名变化时写入一条 system 历史（可追溯，不重复刷屏）。"""
    b = dict(b)
    b["risks"] = compute_risk(b)
    sig = "|".join(b["risks"]) if b["risks"] else ""
    if sig != (b.get("risk_sig") or ""):
        if sig:
            _log(conn, b["id"], kind="system", role="系统",
                 actor="保底机制", comment="风险告警：" + "；".join(b["risks"]))
        conn.execute("UPDATE bugs SET risk_sig=? WHERE id=?", (sig, b["id"]))
        b["risk_sig"] = sig
    return b


# ── 请求模型 ──────────────────────────────────────────
class RegisterReq(BaseModel):
    username: str
    password: str
    role: str = sm.ROLE_TESTER
    display_name: Optional[str] = None
    workspace_code: Optional[str] = None   # 填了则加入该工作区，否则自建
    workspace_name: Optional[str] = None


class LoginReq(BaseModel):
    username: str
    password: str


class BugCreate(BaseModel):
    title: str
    description: str = ""
    reporter: Optional[str] = None         # 缺省用登录身份姓名
    priority: str = "P2-中"
    severity: str = "一般"
    project: str = sm.DEFAULT_PROJECT


class TransitionReq(BaseModel):
    to: str
    comment: str = ""


class FieldEditReq(BaseModel):
    field: str
    value: Optional[str] = None
    blocked_reason: Optional[str] = None
    comment: Optional[str] = None
    expect_updated_at: Optional[str] = None  # 乐观锁


class CommentReq(BaseModel):
    comment: str


class AttachmentReq(BaseModel):
    filename: str
    data: str
    mime: str = "image/png"


# ── 应用 ─────────────────────────────────────────────
app = FastAPI(title="研发缺陷协作系统", version="3.0.0")

active_ws: set[WebSocket] = set()


async def broadcast():
    dead = set()
    for w in list(active_ws):
        try:
            await w.send_text("refresh")
        except Exception:
            dead.add(w)
    active_ws.difference_update(dead)


class AuthMiddleware(BaseHTTPMiddleware):
    """API 鉴权：/api/auth/* 公开；其余 /api 需有效 JWT；页面/静态资源放行。"""
    async def dispatch(self, request: Request, call_next):
        p = request.url.path
        if p.startswith("/api/"):
            if p in ("/api/auth/login", "/api/auth/register", "/api/auth/health"):
                return await call_next(request)
            if _decode_token(_token_from_request(request)) is None:
                return JSONResponse({"detail": "未登录或登录已过期"}, status_code=401)
        return await call_next(request)


app.add_middleware(AuthMiddleware)


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _get_bug(conn: sqlite3.Connection, bug_id: int, wid: int) -> dict:
    row = conn.execute("SELECT * FROM bugs WHERE id=? AND workspace_id=?", (bug_id, wid)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="缺陷不存在或无权访问")
    return _row_to_dict(row)


def get_projects(wid: int) -> list:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT project FROM bugs WHERE workspace_id=? AND project IS NOT NULL AND project!=''",
            (wid,),
        ).fetchall()
        names = [r["project"] for r in rows]
        if sm.DEFAULT_PROJECT not in names:
            names.insert(0, sm.DEFAULT_PROJECT)
        return names
    finally:
        conn.close()


def _backup_db() -> None:
    try:
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        target = BACKUP_DIR / f"defects_{stamp}.db"
        if not target.exists():
            shutil.copy2(DB_PATH, target)
        backups = sorted(BACKUP_DIR.glob("defects_*.db"))
        for old in backups[:-7]:
            old.unlink()
    except Exception as e:
        print(f"[警告] 自动备份失败：{e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db()
    _backup_db()
    print("=" * 50)
    print("研发缺陷协作系统 已启动")
    print("内置管理员账号：admin / admin123（演示工作区，邀请码 DEMO）")
    print("=" * 50)
    yield


app.router.lifespan_context = lifespan


# ── 账号：注册 / 登录 / 身份 ─────────────────────────
@app.post("/api/auth/register", status_code=201)
def register(payload: RegisterReq):
    if payload.role not in sm.ROLES:
        raise HTTPException(status_code=400, detail="角色非法（应为 测试/产品/开发）")
    if len(payload.username) < 2 or len(payload.password) < 4:
        raise HTTPException(status_code=400, detail="用户名≥2位，密码≥4位")
    conn = _connect()
    try:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (payload.username,)).fetchone():
            raise HTTPException(status_code=409, detail="用户名已存在")
        now = _now()
        wid = None
        if payload.workspace_code:
            ws = conn.execute("SELECT * FROM workspaces WHERE code=?", (payload.workspace_code.upper(),)).fetchone()
            if not ws:
                raise HTTPException(status_code=400, detail="邀请码无效")
            wid = ws["id"]
        else:
            code = _gen_code()
            name = (payload.workspace_name or f"{payload.username}的工作区").strip() or f"{payload.username}的工作区"
            cur = conn.execute(
                "INSERT INTO workspaces (name,code,owner_id,created_at) VALUES (?,?,?,?)",
                (name, code, None, now),
            )
            wid = cur.lastrowid
        disp = (payload.display_name or payload.username).strip() or payload.username
        cur = conn.execute(
            "INSERT INTO users (username,password_hash,role,display_name,workspace_id,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (payload.username, _hash_pw(payload.password), payload.role, disp, wid, now),
        )
        uid = cur.lastrowid
        conn.execute("UPDATE workspaces SET owner_id=? WHERE id=?", (uid, wid))
        conn.commit()
        user = {"id": uid, "username": payload.username, "role": payload.role,
                "display_name": disp, "workspace_id": wid}
        return {"token": _make_token(user), "user": user}
    finally:
        conn.close()


@app.post("/api/auth/login")
def login(payload: LoginReq):
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (payload.username,)).fetchone()
        if not row or not _verify_pw(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        user = _row_to_dict(row)
        return {"token": _make_token(user),
                "user": {"id": user["id"], "username": user["username"], "role": user["role"],
                         "display_name": user["display_name"], "workspace_id": user["workspace_id"]}}
    finally:
        conn.close()


@app.get("/api/me")
def me(user=Depends(get_user)):
    conn = _connect()
    try:
        ws = conn.execute("SELECT name,code FROM workspaces WHERE id=?", (user["w"],)).fetchone()
        return {"username": user["u"], "role": user["r"], "display_name": user["n"],
                "workspace_id": user["w"], "workspace_name": ws["name"] if ws else "—",
                "workspace_code": ws["code"] if ws else "—"}
    finally:
        conn.close()


@app.get("/api/auth/health")
def health():
    return {"ok": True}


# ── 元数据：前端渲染状态机 / 权限矩阵 / 配置 ──────────
@app.get("/api/meta")
def get_meta(user=Depends(get_user)):
    return {
        "roles": sm.ROLES,
        "responsibilities": sm.ROLE_RESPONSIBILITIES,
        "states": sm.STATES,
        "terminal_states": sm.TERMINAL_STATES,
        "active_states": sm.ACTIVE_STATES,
        "initial_state": sm.INITIAL_STATE,
        "priorities": sm.PRIORITIES,
        "severities": sm.SEVERITIES,
        "transitions": [
            {"from": f, "to": t, "role": r, "action": a, "desc": d}
            for (f, t), (r, a, d) in sm.TRANSITIONS.items()
        ],
        "field_edit_roles": sm.FIELD_EDIT_ROLES,
        "action_roles": sm.ACTION_ROLES,
        "role_queues": sm.ROLE_QUEUES,
        "default_project": sm.DEFAULT_PROJECT,
        "projects": get_projects(user["w"]),
        "config": sm.CONFIG,
    }


# ── 缺陷 CRUD ─────────────────────────────────────────
@app.get("/api/bugs")
def list_bugs(user=Depends(get_user), status: Optional[str] = None, priority: Optional[str] = None,
              assignee: Optional[str] = None, risk: Optional[int] = 0,
              my: Optional[str] = None, project: Optional[str] = None,
              limit: int = 0, offset: int = 0, q: Optional[str] = None):
    wid = user["w"]
    conn = _connect()
    try:
        where, args = ["workspace_id=?"], [wid]
        if status:
            where.append("status=?"); args.append(status)
        if priority:
            where.append("priority=?"); args.append(priority)
        if assignee:
            where.append("assignee=?"); args.append(assignee)
        if project:
            where.append("project=?"); args.append(project)
        if q:
            where.append("(title LIKE ? OR description LIKE ? OR assignee LIKE ? OR reporter LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like, like, like])
        sql = "SELECT * FROM bugs WHERE " + " AND ".join(where) + " ORDER BY id DESC"
        rows = conn.execute(sql, args).fetchall()
        total = len(rows)
        if limit > 0:
            rows = rows[offset:offset + limit]
        bugs = [_row_to_dict(r) for r in rows]

        if my:
            bugs = [b for b in bugs if _is_my_todo(b, user["r"], my)]

        enriched = []
        for b in bugs:
            b = _enrich(conn, b)
            b["actions"] = sm.allowed_actions(b["status"], user["r"])
            b["can_edit"] = {f: sm.can_edit_field(f, user["r"])[0] for f in sm.FIELD_EDIT_ROLES}
            enriched.append(b)
        conn.commit()
        if risk:
            enriched = [b for b in enriched if b["risks"]]
        return {"total": total, "count": len(enriched), "bugs": enriched}
    finally:
        conn.close()


def _is_my_todo(b: dict, role: str, my: str) -> bool:
    st = b["status"]
    if role == sm.ROLE_TESTER:
        return st in (sm.S_VERIFYING, sm.S_REOPENED)
    if role == sm.ROLE_PRODUCT:
        return st in (sm.S_NEW, sm.S_CONFIRMED)
    if role == sm.ROLE_DEVELOPER:
        if st in (sm.S_FIXING, sm.S_REOPENED):
            return not my or b.get("assignee") == my or not b.get("assignee")
        return st == sm.S_CONFIRMED
    return False


@app.get("/api/bugs/export")
def export_csv(user=Depends(get_user), q: Optional[str] = None, status: Optional[str] = None):
    import csv, io
    wid = user["w"]
    conn = _connect()
    try:
        where, args = ["workspace_id=?"], [wid]
        if status:
            where.append("status=?"); args.append(status)
        if q:
            where.append("(title LIKE ? OR description LIKE ? OR assignee LIKE ? OR reporter LIKE ?)")
            like = f"%{q}%"; args.extend([like, like, like, like])
        sql = "SELECT * FROM bugs WHERE " + " AND ".join(where) + " ORDER BY id DESC"
        rows = conn.execute(sql, args).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ID", "标题", "描述", "状态", "优先级", "严重程度",
                    "报告人", "处理人", "阻塞", "项目", "创建时间", "更新时间"])
        for r in rows:
            w.writerow([r["id"], r["title"], r["description"], r["status"], r["priority"],
                        r["severity"], r["reporter"], r["assignee"] or "",
                        "是" if r["blocked"] else "", r["project"] or sm.DEFAULT_PROJECT,
                        r["created_at"], r["updated_at"]])
        return Response(content="\ufeff" + buf.getvalue(),
                        media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=defects_export.csv"})
    finally:
        conn.close()


@app.get("/api/bugs/{bug_id}")
def get_bug(bug_id: int, user=Depends(get_user)):
    conn = _connect()
    try:
        b = _enrich(conn, _get_bug(conn, bug_id, user["w"]))
        conn.commit()
        b["actions"] = sm.allowed_actions(b["status"], user["r"])
        b["can_edit"] = {f: sm.can_edit_field(f, user["r"])[0] for f in sm.FIELD_EDIT_ROLES}
        hist = conn.execute(
            "SELECT * FROM history WHERE bug_id=? ORDER BY id ASC", (bug_id,)
        ).fetchall()
        atts = conn.execute(
            "SELECT id,filename,mime,size,by_role,actor,created_at FROM attachments"
            " WHERE bug_id=? ORDER BY id DESC", (bug_id,),
        ).fetchall()
        return {"bug": b, "history": [_row_to_dict(h) for h in hist],
                "attachments": [_row_to_dict(a) for a in atts]}
    finally:
        conn.close()


@app.delete("/api/bugs/{bug_id}")
def delete_bug(bug_id: int, user=Depends(get_user)):
    if user["r"] != sm.ROLE_PRODUCT:
        raise HTTPException(status_code=403, detail="删除缺陷只能由【产品】执行")
    conn = _connect()
    try:
        b = _get_bug(conn, bug_id, user["w"])
        hist = conn.execute("SELECT COUNT(*) AS c FROM history WHERE bug_id=?", (bug_id,)).fetchone()["c"]
        conn.execute(
            "INSERT INTO audit_log (action,bug_id,bug_title,detail,by_role,actor,workspace_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("delete_bug", bug_id, b["title"], f"状态={b['status']}，留痕 {hist} 条，一并归档",
             user["r"], user["n"], user["w"], _now()),
        )
        conn.execute("DELETE FROM attachments WHERE bug_id=?", (bug_id,))
        conn.execute("DELETE FROM history WHERE bug_id=?", (bug_id,))
        conn.execute("DELETE FROM bugs WHERE id=? AND workspace_id=?", (bug_id, user["w"]))
        conn.commit()
        return {"ok": True, "deleted": bug_id, "archived_history": hist}
    finally:
        conn.close()


@app.get("/api/audit")
def audit_log(user=Depends(get_user), limit: int = 50):
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE workspace_id=? ORDER BY id DESC LIMIT ?",
            (user["w"], limit),
        ).fetchall()
        return {"events": [_row_to_dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/bugs", status_code=201)
def create_bug(payload: BugCreate, user=Depends(get_user)):
    ok, msg = sm.can_action("create_bug", user["r"])
    if not ok:
        raise HTTPException(status_code=403, detail=msg)
    if payload.priority not in sm.PRIORITIES or payload.severity not in sm.SEVERITIES:
        raise HTTPException(status_code=400, detail="优先级或严重程度取值非法")
    now = _now()
    reporter = (payload.reporter or user["n"]).strip() or user["n"]
    project = (payload.project or sm.DEFAULT_PROJECT).strip() or sm.DEFAULT_PROJECT
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO bugs (workspace_id,title,description,reporter,priority,severity,assignee,status,project,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user["w"], payload.title, payload.description, reporter,
             payload.priority, payload.severity, "", sm.INITIAL_STATE, project, now, now),
        )
        bid = cur.lastrowid
        _log(conn, bid, kind="status", to=sm.INITIAL_STATE, role=user["r"],
             actor=user["n"], comment="创建缺陷")
        conn.commit()
        return _get_bug(conn, bid, user["w"])
    finally:
        conn.close()


@app.post("/api/bugs/{bug_id}/transition")
async def transition(bug_id: int, payload: TransitionReq, user=Depends(get_user)):
    conn = _connect()
    try:
        b = _get_bug(conn, bug_id, user["w"])
        frm = b["status"]
        ok, msg, code = sm.can_transition(frm, payload.to, user["r"])
        if not ok:
            raise HTTPException(status_code=code, detail=msg)
        now = _now()
        reopen_inc = 1 if payload.to == sm.S_REOPENED else 0
        if payload.to == sm.S_FIXING and not b.get("assignee"):
            conn.execute("UPDATE bugs SET assignee=? WHERE id=?", (user["n"], bug_id))
        conn.execute(
            "UPDATE bugs SET status=?, updated_at=?, reopen_count=reopen_count+? WHERE id=? AND workspace_id=?",
            (payload.to, now, reopen_inc, bug_id, user["w"]),
        )
        _log(conn, bug_id, kind="status", frm=frm, to=payload.to,
             role=user["r"], actor=user["n"], comment=payload.comment or msg)
        conn.commit()
        result = _get_bug(conn, bug_id, user["w"])
    finally:
        conn.close()
    await broadcast()
    return result


@app.patch("/api/bugs/{bug_id}")
async def edit_field(bug_id: int, payload: FieldEditReq, user=Depends(get_user)):
    conn = _connect()
    try:
        b = _get_bug(conn, bug_id, user["w"])
        if payload.expect_updated_at and payload.expect_updated_at != b["updated_at"]:
            raise HTTPException(status_code=409,
                detail=f"该缺陷刚被他人修改过（最新更新时间 {b['updated_at']}），请刷新后重试")
        ok, msg = sm.can_edit_field(payload.field, user["r"])
        if not ok:
            raise HTTPException(status_code=403, detail=msg)
        now = _now()
        if payload.field in ("priority", "severity"):
            if payload.field == "priority" and payload.value not in sm.PRIORITIES:
                raise HTTPException(status_code=400, detail="优先级取值非法")
            if payload.field == "severity" and payload.value not in sm.SEVERITIES:
                raise HTTPException(status_code=400, detail="严重程度取值非法")
            conn.execute(f"UPDATE bugs SET {payload.field}=?, updated_at=? WHERE id=? AND workspace_id=?",
                         (payload.value, now, bug_id, user["w"]))
            _log(conn, bug_id, kind="field", field=payload.field, old=b.get(payload.field),
                 new=payload.value, role=user["r"], actor=user["n"], comment=payload.comment or "")
        elif payload.field == "assignee":
            conn.execute("UPDATE bugs SET assignee=?, updated_at=? WHERE id=? AND workspace_id=?",
                         (payload.value or None, now, bug_id, user["w"]))
            _log(conn, bug_id, kind="field", field="assignee", old=b.get("assignee"),
                 new=payload.value or "（未指派）", role=user["r"], actor=user["n"],
                 comment=payload.comment or "")
        elif payload.field == "project":
            val = (payload.value or "").strip()
            if not val or len(val) > 50:
                raise HTTPException(status_code=400, detail="项目名不能为空且不超过 50 字")
            conn.execute("UPDATE bugs SET project=?, updated_at=? WHERE id=? AND workspace_id=?",
                         (val, now, bug_id, user["w"]))
            _log(conn, bug_id, kind="field", field="project", old=b.get("project"), new=val,
                 role=user["r"], actor=user["n"], comment=payload.comment or "变更项目")
        elif payload.field == "blocked":
            blk = 1 if str(payload.value) in ("1", "true", "True", "yes") else 0
            conn.execute("UPDATE bugs SET blocked=?, blocked_reason=?, updated_at=? WHERE id=? AND workspace_id=?",
                         (blk, payload.blocked_reason or None, now, bug_id, user["w"]))
            _log(conn, bug_id, kind="field", field="blocked", old=b.get("blocked"), new=blk,
                 role=user["r"], actor=user["n"], comment=payload.blocked_reason or ("标记阻塞" if blk else "解除阻塞"))
        elif payload.field in ("title", "description"):
            val = (payload.value or "").strip()
            if not val:
                raise HTTPException(status_code=400, detail="标题/描述不能为空")
            conn.execute(f"UPDATE bugs SET {payload.field}=?, updated_at=? WHERE id=? AND workspace_id=?",
                         (val, now, bug_id, user["w"]))
            _log(conn, bug_id, kind="field", field=payload.field, old=b.get(payload.field), new=val,
                 role=user["r"], actor=user["n"], comment=payload.comment or f"编辑{payload.field}")
        conn.commit()
        result = _get_bug(conn, bug_id, user["w"])
    finally:
        conn.close()
    await broadcast()
    return result


@app.post("/api/bugs/{bug_id}/comment")
async def add_comment(bug_id: int, payload: CommentReq, user=Depends(get_user)):
    ok, msg = sm.can_action("comment", user["r"])
    if not ok:
        raise HTTPException(status_code=403, detail=msg)
    if not payload.comment.strip():
        raise HTTPException(status_code=400, detail="评论内容不能为空")
    conn = _connect()
    try:
        _get_bug(conn, bug_id, user["w"])
        _log(conn, bug_id, kind="comment", role=user["r"], actor=user["n"], comment=payload.comment.strip())
        conn.commit()
    finally:
        conn.close()
    await broadcast()
    return {"ok": True}


MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


@app.post("/api/bugs/{bug_id}/attachments", status_code=201)
async def upload_attachment(bug_id: int, payload: AttachmentReq, user=Depends(get_user)):
    import base64 as b64
    ok, msg = sm.can_action("comment", user["r"])
    if not ok:
        raise HTTPException(status_code=403, detail=msg)
    try:
        raw = b64.b64decode(payload.data, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="附件数据不是合法的 base64")
    if not raw:
        raise HTTPException(status_code=400, detail="附件内容为空")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=400, detail="附件超过 2MB 上限，请压缩后重试")
    conn = _connect()
    try:
        _get_bug(conn, bug_id, user["w"])
        now = _now()
        cur = conn.execute(
            "INSERT INTO attachments (bug_id,filename,mime,size,data,by_role,actor,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (bug_id, payload.filename[:120], payload.mime, len(raw), payload.data,
             user["r"], user["n"], now),
        )
        _log(conn, bug_id, kind="file", role=user["r"], actor=user["n"],
             comment=f"上传附件 {payload.filename[:120]}（{len(raw) // 1024}KB）")
        conn.commit()
        result = {"ok": True, "id": cur.lastrowid, "size": len(raw)}
    finally:
        conn.close()
    await broadcast()
    return result


@app.get("/api/attachments/{att_id}")
def get_attachment(att_id: int, user=Depends(get_user)):
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT a.* FROM attachments a JOIN bugs b ON a.bug_id=b.id "
            "WHERE a.id=? AND b.workspace_id=?", (att_id, user["w"])
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="附件不存在")
        import base64 as b64
        from urllib.parse import quote
        fn = row["filename"]
        disp = f"inline; filename*=utf-8''{quote(fn)}"
        return Response(content=b64.b64decode(row["data"]), media_type=row["mime"],
                        headers={"Content-Disposition": disp})
    finally:
        conn.close()


@app.get("/api/dashboard")
def dashboard(user=Depends(get_user)):
    wid = user["w"]
    conn = _connect()
    try:
        bugs = [_row_to_dict(r) for r in
                conn.execute("SELECT * FROM bugs WHERE workspace_id=?", (wid,)).fetchall()]
        by_status, by_priority, by_assignee = {}, {}, {}
        risk_list, reopen_overflow = [], []
        for b in bugs:
            by_status[b["status"]] = by_status.get(b["status"], 0) + 1
            by_priority[b["priority"]] = by_priority.get(b["priority"], 0) + 1
            a = b.get("assignee") or "（未指派）"
            by_assignee[a] = by_assignee.get(a, 0) + 1
            risks = compute_risk(b)
            if risks:
                risk_list.append({"id": b["id"], "title": b["title"], "status": b["status"], "risks": risks})
            if b["reopen_count"] >= sm.CONFIG["reopen_limit"]:
                reopen_overflow.append({"id": b["id"], "title": b["title"], "reopen_count": b["reopen_count"]})
        open_count = sum(v for k, v in by_status.items() if k not in sm.TERMINAL_STATES)
        role_progress = {}
        for r, queue in sm.ROLE_QUEUES.items():
            role_progress[r] = {"responsibility": sm.ROLE_RESPONSIBILITIES[r],
                                "queues": {s: by_status.get(s, 0) for s in queue}}
        closed_durations = []
        for b in bugs:
            if b["status"] == sm.S_CLOSED:
                try:
                    h = (datetime.strptime(b["updated_at"], "%Y-%m-%d %H:%M:%S")
                         - datetime.strptime(b["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600.0
                    closed_durations.append(h)
                except Exception:
                    pass
        avg_fix = round(sum(closed_durations) / len(closed_durations), 1) if closed_durations else None
        return {
            "total": len(bugs), "open": open_count,
            "closed": by_status.get(sm.S_CLOSED, 0), "rejected": by_status.get(sm.S_REJECTED, 0),
            "by_status": by_status, "by_priority": by_priority, "by_assignee": by_assignee,
            "role_progress": role_progress, "avg_fix_hours": avg_fix,
            "risk_list": sorted(risk_list, key=lambda x: -len(x["risks"])),
            "reopen_overflow": reopen_overflow, "generated_at": _now(),
        }
    finally:
        conn.close()


@app.get("/api/activity")
def activity(user=Depends(get_user), limit: int = 30, since: Optional[str] = None):
    wid = user["w"]
    conn = _connect()
    try:
        if since:
            rows = conn.execute(
                "SELECT h.* FROM history h JOIN bugs b ON h.bug_id=b.id "
                "WHERE b.workspace_id=? AND h.created_at>? ORDER BY h.id DESC LIMIT ?",
                (wid, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT h.* FROM history h JOIN bugs b ON h.bug_id=b.id "
                "WHERE b.workspace_id=? ORDER BY h.id DESC LIMIT ?", (wid, limit),
            ).fetchall()
        return {"events": [_row_to_dict(r) for r in rows][::-1]}
    finally:
        conn.close()


@app.get("/api/backup")
def backup_json(user=Depends(get_user)):
    wid = user["w"]
    conn = _connect()
    try:
        data = {
            "exported_at": _now(),
            "version": app.version,
            "workspace_id": wid,
            "bugs": [_row_to_dict(r) for r in
                     conn.execute("SELECT * FROM bugs WHERE workspace_id=?", (wid,)).fetchall()],
            "history": [_row_to_dict(r) for r in
                        conn.execute("SELECT h.* FROM history h JOIN bugs b ON h.bug_id=b.id "
                                     "WHERE b.workspace_id=?", (wid,)).fetchall()],
            "audit_log": [_row_to_dict(r) for r in
                          conn.execute("SELECT a.* FROM audit_log a WHERE a.bug_id IN "
                                       "(SELECT id FROM bugs WHERE workspace_id=?)", (wid,)).fetchall()],
            "attachments": [_row_to_dict(r) for r in
                            conn.execute("SELECT a.id,a.bug_id,a.filename,a.mime,a.size,a.by_role,a.actor,a.created_at "
                                         "FROM attachments a JOIN bugs b ON a.bug_id=b.id "
                                         "WHERE b.workspace_id=?", (wid,)).fetchall()],
        }
    finally:
        conn.close()
    content = JSONResponse(content=data).body
    return Response(content=content, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=defects_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.json"})


# ── WebSocket 实时推送 ───────────────────────────────
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    token = ws.query_params.get("token") or ""
    u = _decode_token(token)
    if not u:
        await ws.close(code=1008)
        return
    await ws.accept()
    active_ws.add(ws)
    try:
        while True:
            await ws.receive_text()  # 客户端保活；服务端主动推送 refresh
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        active_ws.discard(ws)


# ── 静态页面 ──────────────────────────────────────────
@app.get("/")
def index(request: Request):
    if _decode_token(_token_from_request(request)) is None:
        if LOGIN_HTML.exists():
            return FileResponse(LOGIN_HTML)
        return HTMLResponse("<h1>未配置登录页</h1>")
    return FileResponse(STATIC / "index.html")


@app.get("/style.css")
def style():
    return FileResponse(STATIC / "style.css", media_type="text/css; charset=utf-8")


@app.get("/app.js")
def script():
    return FileResponse(STATIC / "app.js", media_type="text/javascript; charset=utf-8")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
