# common/auth.py
import sqlite3
import os

DB_PATH = "users.db"

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
    # 插入示例数据（如果不存在）
    sample_users = [
        ("alice", "Alice Wang", "1234", "产品部", "产品经理", "manager", "alice"),
        ("bob", "Bob Zhang", "1234", "研发一部", "高级工程师", "developer", "default"),
        ("carol", "Carol Li", "1234", "营运部", "运营总监", "admin", "default"),
    ]
    for user in sample_users:
        try:
            c.execute("INSERT INTO users (username, display_name, pin, department, position, role, tenant) VALUES (?,?,?,?,?,?,?)", user)
        except sqlite3.IntegrityError:
            pass  # 已存在，跳过
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
            "tenant": row[5] or row[0],  # 如果 tenant 为空，默认使用 username 作为租户
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