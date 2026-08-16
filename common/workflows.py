# common/workflows.py
"""
低代码工作流管理模块
允许管理员定义简单的工作流（顺序执行多个工具），
并在对话中通过 execute_workflow 工具调用。
"""

import sqlite3
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

DB_PATH = "workflows.db"

def init_workflows_db():
    """初始化工作流数据库"""
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                steps_json TEXT NOT NULL,
                created_by TEXT,
                created_at TEXT
            )
        ''')
        conn.commit()

def add_workflow(name, description, steps, created_by):
    init_workflows_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO workflows (name, description, steps_json, created_by, created_at) VALUES (?,?,?,?,?)",
                (name, description, json.dumps(steps, ensure_ascii=False), created_by, datetime.now().isoformat())
            )
            conn.commit()
        print(f"[Workflow] 添加成功: {name}", flush=True)
        return True
    except sqlite3.IntegrityError:
        print(f"[Workflow] 添加失败: 名称已存在 {name}", flush=True)
        return False
    except Exception as e:
        print(f"[Workflow] 添加异常: {e}", flush=True)
        return False

def get_workflow(name: str) -> Optional[Dict[str, Any]]:
    """根据名称获取工作流定义"""
    init_workflows_db()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT name, description, steps_json FROM workflows WHERE name=?", (name,))
        row = c.fetchone()
    if row:
        return {
            "name": row[0],
            "description": row[1],
            "steps": json.loads(row[2])
        }
    return None

def list_workflows():
    init_workflows_db()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT name, description, created_by, created_at FROM workflows ORDER BY created_at DESC")
        rows = c.fetchall()
    print(f"[Workflow] 列出工作流，共 {len(rows)} 条", flush=True)
    return [{"name": r[0], "description": r[1], "created_by": r[2], "created_at": r[3]} for r in rows]

def delete_workflow(name: str) -> bool:
    """删除指定名称的工作流"""
    init_workflows_db()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM workflows WHERE name=?", (name,))
        conn.commit()
        return c.rowcount > 0

def execute_workflow(name: str, user_session_id: str = "", extra_params: Optional[Dict[str, Any]] = None) -> str:
    """
    执行工作流。按顺序执行步骤，每步调用对应的工具函数。
    返回各步骤结果的汇总文本。
    """
    workflow = get_workflow(name)
    if not workflow:
        return f"工作流 '{name}' 不存在。"

    results = []
    for i, step in enumerate(workflow["steps"], 1):
        tool_name = step.get("tool")
        arguments = step.get("arguments", {})
        # 如果提供了额外参数，合并（例如用户传入的动态值）
        if extra_params:
            arguments.update(extra_params)

        if tool_name not in AVAILABLE_TOOLS:
            results.append(f"步骤{i}: 工具 {tool_name} 未找到")
            continue
        try:
            func = AVAILABLE_TOOLS[tool_name]
            result = func(**arguments)
            results.append(f"步骤{i} ({tool_name}): {str(result)[:200]}")
        except Exception as e:
            results.append(f"步骤{i} ({tool_name}): 执行失败: {e}")

    return "\n".join(results)