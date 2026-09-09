"""缺陷状态机 + 权限矩阵 + 可拓展配置 —— 单一事实来源（后端校验 / 前端渲染共用）。

设计目标（对应需求）：
1. 三角色具体分工：ROLE_RESPONSIBILITIES
2. 各位置权限控制：TRANSITIONS(流转角色) + FIELD_EDIT_ROLES(字段编辑) + ACTION_ROLES(动作)
3. 可追溯：所有变更经 history 记录（谁/角色/时间）
4. 可拓展：所有角色/状态/流转/阈值集中在本文，前端按 /api/meta 渲染，新增只改这里
5. 保底机制：CONFIG 中的 SLA 阈值、重开上限，由后端 compute_risk 实时计算告警
"""

# ── 角色与具体分工 ───────────────────────────────────
ROLE_TESTER = "测试"
ROLE_PRODUCT = "产品"
ROLE_DEVELOPER = "开发"
ROLES = [ROLE_TESTER, ROLE_PRODUCT, ROLE_DEVELOPER]

ROLE_RESPONSIBILITIES = {
    ROLE_TESTER:   "提交缺陷、复测验证（关闭/重开）、跟踪质量与回归",
    ROLE_PRODUCT:  "确认/拒绝、定优先级与严重程度、指派开发、删除归档",
    ROLE_DEVELOPER:"认领、修复、提交验证、标记阻塞",
}

# ── 状态 ─────────────────────────────────────────────
S_NEW = "新建"          # 测试刚提交
S_CONFIRMED = "已确认"  # 产品确认是有效缺陷并指派开发
S_REJECTED = "已拒绝"   # 终态：非缺陷/重复/暂不修复
S_FIXING = "修复中"     # 开发认领并在修
S_VERIFYING = "待验证"  # 开发修完，等测试复测
S_CLOSED = "已关闭"     # 终态：测试复测通过
S_REOPENED = "重新打开" # 测试复测不通过

STATES = [S_NEW, S_CONFIRMED, S_REJECTED, S_FIXING, S_VERIFYING, S_CLOSED, S_REOPENED]
TERMINAL_STATES = [S_REJECTED, S_CLOSED]
INITIAL_STATE = S_NEW
# 需要被「盯着」的活动状态（用于我的待办 / 保底）
ACTIVE_STATES = [S_NEW, S_CONFIRMED, S_FIXING, S_VERIFYING, S_REOPENED]

PRIORITIES = ["P0-紧急", "P1-高", "P2-中", "P3-低"]
SEVERITIES = ["致命", "严重", "一般", "轻微"]

# 每个角色「该盯着」的状态（共同进度页按此划分三方各自的队列）
ROLE_QUEUES = {
    ROLE_TESTER:   [S_VERIFYING, S_CLOSED],           # 待验证的要复测；已关闭的做回归关注
    ROLE_PRODUCT:  [S_NEW, S_CONFIRMED],              # 待确认 / 已确认未指派
    ROLE_DEVELOPER:[S_CONFIRMED, S_FIXING, S_REOPENED],  # 待认领 / 修复中 / 被重开
}

# ── 流转规则：(from,to) -> (责任人角色, 动作名, 说明) ──
TRANSITIONS = {
    (S_NEW, S_CONFIRMED): (ROLE_PRODUCT, "确认并指派", "产品确认是有效缺陷，指派给开发"),
    (S_NEW, S_REJECTED):  (ROLE_PRODUCT, "拒绝", "判定为非缺陷 / 重复 / 暂不修复"),
    (S_CONFIRMED, S_FIXING): (ROLE_DEVELOPER, "开始修复", "开发认领缺陷并开始修复"),
    (S_FIXING, S_VERIFYING): (ROLE_DEVELOPER, "提交修复", "开发修复完成，提交测试复测"),
    (S_VERIFYING, S_CLOSED): (ROLE_TESTER, "验证通过", "测试复测通过，关闭缺陷"),
    (S_VERIFYING, S_REOPENED): (ROLE_TESTER, "验证不通过", "复测仍有问题，重新打开"),
    (S_REOPENED, S_FIXING): (ROLE_DEVELOPER, "重新修复", "开发针对复测问题再次修复"),
}

# ── 字段编辑权限：字段 -> 允许角色 ──────────────────
FIELD_EDIT_ROLES = {
    "priority": [ROLE_PRODUCT],        # 优先级：仅产品
    "severity": [ROLE_PRODUCT],        # 严重程度：仅产品
    "assignee": [ROLE_PRODUCT],        # 指派：仅产品（确认后指派开发）
    "blocked":  [ROLE_DEVELOPER],      # 阻塞标记：仅开发（开发最清楚是否卡住）
    "project":  [ROLE_PRODUCT],        # 所属项目：仅产品（多项目预留）
    # 标题/描述：三方均可补充（协作记录，改动全部留痕），但标题不可清空
    "title":       [ROLE_PRODUCT, ROLE_TESTER, ROLE_DEVELOPER],
    "description": [ROLE_PRODUCT, ROLE_TESTER, ROLE_DEVELOPER],
}

# 多项目预留：默认项目名（projects 列表以后端库里的实际取值为准）
DEFAULT_PROJECT = "默认项目"

# ── 动作权限：动作 -> 允许角色 ──────────────────────
ACTION_ROLES = {
    "create_bug": ROLES,               # 新建缺陷：三方均可登记（测试为主提交方）
    "comment":    ROLES,               # 评论协作：三方皆可
    "reject":     [ROLE_PRODUCT],      # 拒绝：仅产品
    "close":      [ROLE_TESTER],       # 关闭：仅测试
    "reopen":     [ROLE_TESTER],       # 重开：仅测试
}

# ── 可拓展配置（保底机制的阈值都放这里）──────────────
CONFIG = {
    "sla_verify_hours": 48,   # 待验证 超过此时长未复测 → 验证超时
    "sla_fix_hours": 72,      # 修复中/重新打开 超过此时长未推进 → 修复超时
    "reopen_limit": 3,        # 重开次数达到此值 → 反复重开告警
    "auto_refresh_sec": 5,    # 前端实时监控轮询间隔
}

EVENT_KINDS = ["status", "comment", "field", "system", "file"]


# ── 校验函数 ─────────────────────────────────────────
def can_transition(frm: str, to: str, role: str):
    """返回 (ok, msg, http_code)。

    http_code 区分两类失败，便于调用方给出正确语义：
      400 —— 请求本身不合法（状态不存在 / 未变化 / 这条流转不存在）
      403 —— 流转存在但当前角色无权执行（越权，不是请求写错了）
    """
    if frm not in STATES or to not in STATES:
        return False, "状态不合法", 400
    if frm == to:
        return False, "状态未变化", 400
    rule = TRANSITIONS.get((frm, to))
    if not rule:
        return False, f"不允许的流转：{frm} → {to}", 400
    required_role, action, _ = rule
    if required_role != role:
        return False, f"该流转只能由【{required_role}】执行（当前：{role}）", 403
    return True, action, 200


def can_edit_field(field: str, role: str):
    roles = FIELD_EDIT_ROLES.get(field)
    if not roles:
        return False, f"字段「{field}」不可编辑"
    if role not in roles:
        return False, f"只有【{'/'.join(roles)}】可修改「{field}」"
    return True, ""


def can_action(action: str, role: str):
    roles = ACTION_ROLES.get(action)
    if not roles:
        return False, f"未知动作「{action}」"
    if role not in roles:
        return False, f"「{action}」只能由【{'/'.join(roles)}】执行（当前：{role}）"
    return True, ""


def allowed_actions(frm: str, role: str):
    out = []
    for (a, b), (r, act, desc) in TRANSITIONS.items():
        if a == frm and r == role:
            out.append({"to": b, "action": act, "desc": desc})
    return out


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
