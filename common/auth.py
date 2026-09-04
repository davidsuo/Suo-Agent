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
        "analyze_file", "speech_to_text", "send_email", "execute_python"
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


def init_users_db():
    import sqlite3
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            pin TEXT,
            real_name TEXT DEFAULT '',
            role TEXT DEFAULT 'viewer',
            department TEXT DEFAULT '',
            contact TEXT DEFAULT '',
            status TEXT DEFAULT '正常'
        )
    ''')
    # 插入默认用户
    default_users = [
        ("alice", "1234", "Alice Wang", "manager", "产品部", "13800138000", "正常"),
        ("bob", "1234", "Bob Zhang", "developer", "研发部", "13900139000", "禁用"),
        ("carol", "1234", "Carol Li", "admin", "市场部", "13700137000", "正常"),
    ]
    for u in default_users:
        try:
            cursor.execute("INSERT INTO users (username, pin, real_name, role, department, contact, status) VALUES (?, ?, ?, ?, ?, ?, ?)", u)
        except:
            pass
    conn.commit()
    conn.close()
    print("✅ 用户数据库初始化完成")


def get_user_info(username: str):
    import sqlite3
    try:
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT username, pin, real_name, role, department, contact, status FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        if row:
            # 如果用户状态为“禁用”，直接拒绝登录
            if row[6] == '禁用':
                print(f"###DEBUG### 用户 {username} 已被禁用，拒绝登录")
                return None
            return {
                "username": row[0],
                "pin": row[1],
                "real_name": row[2],
                "display_name": row[2],
                "role": row[3],
                "department": row[4],
                "contact": row[5],
                "status": row[6]
            }
    except Exception as e:
        print(f"###DEBUG### 获取用户失败: {e}")
    return None

def authenticate(username: str, pin: str):
    import sqlite3
    try:
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT username, pin, real_name, role, department, contact, status FROM users WHERE username = ? AND pin = ?", (username, pin))
        row = cursor.fetchone()
        conn.close()
        if row:
            # 如果用户状态为“禁用”，返回特定的标志让主程序处理
            if row[6] == '禁用':
                return {"status": "disabled"}
            return {
                "username": row[0],
                "pin": row[1],
                "real_name": row[2],
                "display_name": row[2],
                "role": row[3],
                "department": row[4],
                "contact": row[5],
                "status": row[6]
            }
    except Exception as e:
        print(f"###DEBUG### 登录验证失败: {e}")
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