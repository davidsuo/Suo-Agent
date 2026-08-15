# common/pending_tools.py
"""
待确认操作持久化模块

用于存储需要用户二次确认的危险操作（如发送邮件）。
确保服务重启后待确认项不丢失，并提供线程安全的读写访问。
"""

import json
import os
import threading
import tempfile
from typing import Any, Dict

PENDING_FILE = "pending.json"

# 线程锁，保护全局 pending 字典的读写
_lock = threading.Lock()

def load_pending() -> Dict[str, Any]:
    """
    从 JSON 文件加载待确认操作。

    如果文件不存在或损坏，返回空字典。
    """
    if not os.path.exists(PENDING_FILE):
        return {}
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as e:
        print(f"[Pending] 加载待确认项失败: {e}，使用空字典")
        return {}

def save_pending(data: Dict[str, Any]) -> None:
    """
    原子地将待确认操作保存到 JSON 文件。

    使用临时文件 + os.replace 确保文件不会损坏。
    """
    try:
        # 先写入临时文件
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(PENDING_FILE) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        # 原子替换
        os.replace(tmp_path, PENDING_FILE)
        print(f"[Pending] 待确认项已保存，当前数量: {len(data)}")
    except Exception as e:
        print(f"[Pending] 保存待确认项失败: {e}")

# ==================== 全局待确认字典（线程安全访问） ====================
# 启动时加载
pending: Dict[str, Any] = load_pending()

# 以下函数提供更安全的访问方式，但为了兼容，仍保留直接操作 pending 的可能
def set_pending(session_id: str, tool_info: Dict[str, Any]) -> None:
    """添加或更新某个会话的待确认操作"""
    with _lock:
        pending[session_id] = tool_info
        save_pending(pending)

def get_pending(session_id: str) -> Dict[str, Any]:
    """获取某个会话的待确认操作，不存在返回空字典"""
    with _lock:
        return pending.get(session_id, {})

def pop_pending(session_id: str) -> Dict[str, Any]:
    """移除并返回某个会话的待确认操作"""
    with _lock:
        info = pending.pop(session_id, {})
        save_pending(pending)
        return info

def clear_pending(session_id: str) -> None:
    """清除某个会话的待确认操作"""
    with _lock:
        if session_id in pending:
            del pending[session_id]
            save_pending(pending)