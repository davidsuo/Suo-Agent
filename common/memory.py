# common/memory.py
"""
会话记忆与持久化模块

职责：
1. 存储多租户、多用户的对话历史
2. 维护租户映射和当前用户信息
3. 将记忆持久化到本地 JSON 文件，支持跨请求恢复
4. 提供线程安全的数据访问，防止并发冲突
"""

import json
import os
import threading
import tempfile
from typing import Any, Dict, List, Optional

class ConversationMemory:
    """多租户会话记忆管理器，支持持久化和线程安全"""

    def __init__(self, storage_path: str = "memory_state.json"):
        self.storage_path = storage_path
        self.sessions: Dict[str, List[Dict[str, str]]] = {}
        self.tenant_map: Dict[str, str] = {}
        self.all_tenants: set = {"default"}
        self.current_user: Optional[Dict[str, str]] = None
        self._lock = threading.Lock()          # 保护共享状态
        self.load_from_file()
        self.file_contexts: Dict[str, str] = {}

    # ================== 持久化核心 ==================
    def _save_to_file(self) -> None:
        """将当前状态原子地保存到 JSON 文件（临时文件+替换）"""
        data = {
            "sessions": self.sessions,
            "tenant_map": self.tenant_map,
            "all_tenants": list(self.all_tenants),
            "current_user": self.current_user,
            "file_contexts": self.file_contexts,
        }
        try:
            # 先写入临时文件，再原子替换，避免进程中断导致文件损坏
            fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(self.storage_path) or ".")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.storage_path)
            print(f"[Memory] 保存成功，会话总数: {len(self.sessions)}")
        except Exception as e:
            print(f"[Memory] 保存失败: {e}")

    def load_from_file(self) -> None:
        """从 JSON 文件加载记忆状态"""
        if not os.path.exists(self.storage_path):
            print("[Memory] 文件不存在，使用默认空记忆")
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.sessions = data.get("sessions", {})
            self.tenant_map = data.get("tenant_map", {})
            loaded_tenants = data.get("all_tenants", ["default"])
            self.all_tenants = set(loaded_tenants) if loaded_tenants else {"default"}
            self.current_user = data.get("current_user", None)
            self.file_contexts = data.get("file_contexts", {})
            print(f"[Memory] 加载成功，租户映射: {self.tenant_map}, 会话键数量: {len(self.sessions)}")
        except Exception as e:
            print(f"[Memory] 加载失败: {e}，将使用空记忆并可能覆盖旧文件")

    # ================== 租户管理 ==================
    def set_tenant(self, session_id: str, tenant_id: str) -> None:
        """设置某个会话ID对应的租户"""
        with self._lock:
            self.tenant_map[session_id] = tenant_id
            self.all_tenants.add(tenant_id)
            self._save_to_file()

    def get_tenant(self, session_id: str) -> str:
        """获取会话ID对应的租户，默认 'default'"""
        return self.tenant_map.get(session_id, "default")
        
    def set_file_context(self, session_id: str, content: str) -> None:
        """设置某个会话的文件内容（仅供模型使用，不显示在聊天历史）"""
        with self._lock:
            self.file_contexts[session_id] = content
            self._save_to_file()

    def get_file_context(self, session_id: str) -> str:
        """获取某个会话的文件内容，若不存在返回空字符串"""
        return self.file_contexts.get(session_id, "")

    # ================== 会话历史管理 ==================
    def _get_session_key(self, session_id: str) -> str:
        """生成会话键："{tenant}:{session_id}"，确保租户隔离"""
        return f"{self.get_tenant(session_id)}:{session_id}"

    def get(self, session_id: str) -> List[Dict[str, str]]:
        """获取会话历史副本，避免外部修改内部数据"""
        key = self._get_session_key(session_id)
        return self.sessions.get(key, []).copy()

    def append(self, session_id: str, user_msg: str, assistant_msg: str) -> None:
        """追加一轮对话（用户消息+助手消息），线程安全"""
        key = self._get_session_key(session_id)
        with self._lock:
            if key not in self.sessions:
                self.sessions[key] = []
            self.sessions[key].append({"role": "user", "content": user_msg})
            self.sessions[key].append({"role": "assistant", "content": assistant_msg})
            self._save_to_file()

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        """获取完整历史记录"""
        return self.get(session_id)

    def set_history(self, session_id: str, history: List[Dict[str, str]]) -> None:
        """直接设置会话历史（覆盖），线程安全"""
        if history:
            key = self._get_session_key(session_id)
            with self._lock:
                self.sessions[key] = history.copy()
                self._save_to_file()

    # ================== 当前用户管理 ==================
    def set_current_user(self, user_info: Optional[Dict[str, str]]) -> None:
        """设置当前登录用户信息，None 表示清除"""
        with self._lock:
            self.current_user = user_info
            self._save_to_file()

    def get_current_user(self) -> Optional[Dict[str, str]]:
        """获取当前登录用户信息，未登录返回 None"""
        return self.current_user

    # 兼容旧接口
    def set_user_info(self, session_id: str, user_info: Dict[str, str]) -> None:
        """兼容方法：设置当前用户（忽略 session_id）"""
        self.set_current_user(user_info)

    def get_user_info(self, session_id: Optional[str] = None) -> Optional[Dict[str, str]]:
        """兼容方法：获取当前用户（忽略 session_id）"""
        return self.get_current_user()

    def clear_user_info(self, session_id: Optional[str] = None) -> None:
        """清除当前用户信息"""
        self.set_current_user(None)


# ================== 全局单例 ==================
memory = ConversationMemory()