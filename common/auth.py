# common/auth.py
"""
用户认证与权限管理模块

职责：
1. 初始化并管理用户数据库 (users.db)
2. 用户认证（用户名 + PIN 码）
3. 查询用户信息（部门、职位、角色、租户）
4. 基于角色的工具权限控制 (RBAC)

注意：当前 PIN 码以明文存储，仅适用于原型阶段。生产环境应使用哈希算法（如 bcrypt）。
"""

import sqlite3
from typing import Dict, List, Optional

DB_PATH = "users.db"

# 企业部门列表（预留，未来可能用于部门级数据隔离）
DEPARTMENTS = [
    "营运部", "产品部", "客服部", "研发一部", "研发二部", "财务部", "市场营销部"
]

# ==================== RBAC 权限映射 ====================
# 角色 -> 允许使用的工具名称列表
# "*" 表示拥有所有工具权限
ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "admin": ["*"],
    "manager": [
        "get_current_time", "calculator", "query_database", "list_events",
        "add_event", "delete_event", "web_search", "fetch_webpage",
        "analyze_file", "speech_to_text", "send_email"
    ],
    "developer": [
        "get_current_time", "calculator", "query_database", "execute_python",
        "web_search", "fetch_webpage", "generate_image", "analyze_file",
        "speech_to_text", "recognize_table", "ocr_image"
    ],
    "viewer": [
        "get_current_time", "calculator", "query_database", "list_events",
        "web_search", "analyze_file"
    ],
}


def _get_db_connection() -> sqlite3.Connection:
    """获取数据库连接（使用 with 语句确保自动关闭）"""
    return sqlite3.connect(DB_PATH)


def _row_to_user_dict(row: tuple) -> Dict[str, str]:
    """将数据库查询行转换为用户信息字典"""
    return {
        "username": row[0],
        "display_name": row[1],
        "department": row[2],
        "position": row[3],
        "role": row[4],
        "tenant": row[5] or row[0],   # tenant 为空时默认使用用户名
    }


def init_users_db() -> None:
    """初始化用户数据库，创建 users 表并插入示例用户（幂等操作）"""
    with _get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT,
                pin TEXT NOT NULL,
                department TEXT,
                position TEXT,
                role TEXT DEFAULT 'viewer',
                tenant TEXT
            )
        ''')

        sample_users = [
            ("alice", "Alice Wang", "1234", "产品部", "产品经理", "manager", "alice"),
            ("bob", "Bob Zhang", "1234", "研发一部", "高级工程师", "developer", "bob"),
            ("carol", "Carol Li", "1234", "营运部", "运营总监", "admin", "carol"),
            ("david", "David Chen", "1234", "财务部", "财务经理", "manager", "david"),
            ("emma", "Emma Liu", "1234", "市场营销部", "市场专员", "viewer", "emma"),
            ("frank", "Frank Xu", "1234", "客服部", "客服主管", "viewer", "frank"),
            ("grace", "Grace Zhao", "1234", "研发二部", "架构师", "developer", "grace"),
        ]
        for user in sample_users:
            try:
                c.execute(
                    "INSERT INTO users (username, display_name, pin, department, position, role, tenant) "
                    "VALUES (?,?,?,?,?,?,?)",
                    user
                )
            except sqlite3.IntegrityError:
                # 用户名已存在，跳过
                pass
        conn.commit()
        print("✅ 用户数据库已初始化")


def authenticate(username: str, pin: str) -> Optional[Dict[str, str]]:
    """
    验证用户凭证。

    参数:
        username: 用户名（小写）
        pin: 用户 PIN 码

    返回:
        成功时返回用户信息字典；失败返回 None。
    """
    with _get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT username, display_name, department, position, role, tenant "
            "FROM users WHERE username=? AND pin=?",
            (username, pin)
        )
        row = c.fetchone()

    if row:
        return _row_to_user_dict(row)
    return None


def get_user_info(username: str) -> Optional[Dict[str, str]]:
    """
    根据用户名查询用户信息（不验证 PIN）。

    参数:
        username: 用户名

    返回:
        用户信息字典；如果用户不存在，返回 None。
    """
    with _get_db_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT username, display_name, department, position, role, tenant "
            "FROM users WHERE username=?",
            (username,)
        )
        row = c.fetchone()

    if row:
        return _row_to_user_dict(row)
    return None


def _get_allowed_tools(role: str) -> Optional[set]:
    """
    返回指定角色允许的工具集合。

    如果角色权限为 ["*"]，返回 None 表示拥有所有权限；
    如果角色不存在，返回空集合。
    """
    allowed = ROLE_PERMISSIONS.get(role, [])
    if allowed == ["*"]:
        return None
    return set(allowed)


def filter_tools_by_role(role: str, all_tools: Dict[str, object]) -> Dict[str, object]:
    """
    根据角色过滤工具字典。

    参数:
        role: 用户角色
        all_tools: 完整的工具名称到函数映射

    返回:
        该角色允许使用的工具子集；拥有所有权限时返回原字典拷贝。
    """
    allowed = _get_allowed_tools(role)
    if allowed is None:
        return all_tools.copy()
    return {name: func for name, func in all_tools.items() if name in allowed}


def is_tool_allowed(role: str, tool_name: str) -> bool:
    """
    检查指定角色是否允许使用某个工具。

    返回 True 表示允许，False 表示禁止。
    """
    allowed = _get_allowed_tools(role)
    if allowed is None:
        return True
    return tool_name in allowed