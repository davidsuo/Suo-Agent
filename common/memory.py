# common/memory.py
import json
import os

class ConversationMemory:
    def __init__(self, storage_path="memory_state.json"):
        self.storage_path = storage_path
        self.sessions = {}
        self.tenant_map = {}
        self.all_tenants = {"default"}
        self.current_user = None   # 新增：当前登录用户信息
        self.load_from_file()

    def _save_to_file(self):
        data = {
            "sessions": self.sessions,
            "tenant_map": self.tenant_map,
            "all_tenants": list(self.all_tenants),
            "current_user": self.current_user,   # 保存当前用户
        }
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Memory] 保存失败: {e}")

    def load_from_file(self):
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.sessions = data.get("sessions", {})
            self.tenant_map = data.get("tenant_map", {})
            loaded_tenants = data.get("all_tenants", ["default"])
            self.all_tenants = set(loaded_tenants) if loaded_tenants else {"default"}
            self.current_user = data.get("current_user", None)   # 加载当前用户
        except Exception as e:
            print(f"[Memory] 加载失败: {e}")

    def set_tenant(self, session_id, tenant_id):
        self.tenant_map[session_id] = tenant_id
        self.all_tenants.add(tenant_id)
        self._save_to_file()

    def get_tenant(self, session_id):
        return self.tenant_map.get(session_id, "default")

    def _get_session_key(self, session_id):
        return f"{self.get_tenant(session_id)}:{session_id}"

    def get(self, session_id: str) -> list:
        return self.sessions.get(self._get_session_key(session_id), [])

    def append(self, session_id: str, user_msg: str, assistant_msg: str):
        key = self._get_session_key(session_id)
        if key not in self.sessions:
            self.sessions[key] = []
        self.sessions[key].append({"role": "user", "content": user_msg})
        self.sessions[key].append({"role": "assistant", "content": assistant_msg})
        self._save_to_file()

    def get_history(self, session_id: str) -> list:
        return self.sessions.get(self._get_session_key(session_id), [])

    def set_history(self, session_id: str, history: list):
        if history:
            key = self._get_session_key(session_id)
            self.sessions[key] = history.copy()
            self._save_to_file()

    def set_user_info(self, session_id, user_info):
        # 兼容旧方法，实际设置 current_user
        self.current_user = user_info
        self._save_to_file()

    def get_user_info(self, session_id=None):
        # 兼容旧方法，返回 current_user
        return self.current_user

    def set_current_user(self, user_info):
        self.current_user = user_info
        self._save_to_file()

    def get_current_user(self):
        return self.current_user
    
    def clear_user_info(self, session_id=None):
        """清除当前登录用户信息（可选参数 session_id 保持兼容）"""
        self.current_user = None
        self._save_to_file()    

# 全局单例
memory = ConversationMemory()