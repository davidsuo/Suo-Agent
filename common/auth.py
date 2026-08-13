# common/auth.py
import sqlite3
import os

DB_PATH = "users.db"

DEPARTMENTS = ["营运部", "产品部", "客服部", "研发一部", "研发二部", "财务部", "市场营销部"]

# ================== RBAC 权限映射 ==================
ROLE_PERMISSIONS = {
    "admin": ["*"],
    "manager": ["*"],   # 经理拥有所有权限，与管理员相同
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

def init_users_db():
    """初始化用户数据库，插入示例用户"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  display_name TEXT,
                  pin TEXT NOT NULL,
                  department TEXT,
                  position TEXT,
                  role TEXT DEFAULT 'viewer',
                  tenant TEXT)''')
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
            c.execute("INSERT INTO users (username, display_name, pin, department, position, role, tenant) VALUES (?,?,?,?,?,?,?)", user)
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    print("✅ 用户数据库已初始化")

def authenticate(username: str, pin: str) -> dict:
    """验证用户，成功返回用户信息字典，失败返回None"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, display_name, department, position, role, tenant FROM users WHERE username=? AND pin=?", (username, pin))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "display_name": row[1],
            "department": row[2],
            "position": row[3],
            "role": row[4],
            "tenant": row[5] or row[0],
        }
    return None

def get_user_info(username: str) -> dict:
    """根据用户名获取用户信息"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, display_name, department, position, role, tenant FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "username": row[0],
            "display_name": row[1],
            "department": row[2],
            "position": row[3],
            "role": row[4],
            "tenant": row[5] or row[0],
        }
    return None

def filter_tools_by_role(role: str, all_tools: dict) -> dict:
    """根据角色过滤工具字典"""
    allowed = ROLE_PERMISSIONS.get(role, [])
    if allowed == ["*"]:
        return all_tools.copy()
    return {name: func for name, func in all_tools.items() if name in allowed}

def is_tool_allowed(role: str, tool_name: str) -> bool:
    """检查某个角色是否允许使用某个工具"""
    allowed = ROLE_PERMISSIONS.get(role, [])
    if allowed == ["*"]:
        return True
    return tool_name in allowed