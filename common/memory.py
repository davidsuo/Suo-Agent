# common/memory.py
import json
import os

class ConversationMemory:
    def __init__(self, storage_path="memory_state.json"):
        self.storage_path = storage_path
        self.sessions = {}
        self.tenant_map = {}
        self.all_tenants = {"default"}
        self.load_from_file()   # 启动时加载历史

    def _save_to_file(self):
        """保存完整状态到文件"""
        try:
            data = {
                "sessions": self.sessions,
                "tenant_map": self.tenant_map,
                "all_tenants": list(self.all_tenants),
            }
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Memory] 保存失败: {e}")

    def load_from_file(self):
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.sessions = data.get("sessions", {})
                self.tenant_map = data.get("tenant_map", {})
                loaded_tenants = data.get("all_tenants", ["default"])
                self.all_tenants = set(loaded_tenants) if loaded_tenants else {"default"}
                print(f"[Memory] 历史记录已加载，tenant_map: {self.tenant_map}, sessions keys: {list(self.sessions.keys())}")
            except Exception as e:
                print(f"[Memory] 加载失败: {e}")


    def set_tenant(self, session_id, tenant_id):
        self.tenant_map[session_id] = tenant_id
        self.all_tenants.add(tenant_id)
        self._save_to_file()

    def get_tenant(self, session_id):
        return self.tenant_map.get(session_id, "default")

    def _get_session_key(self, session_id):
        tenant = self.get_tenant(session_id)
        return f"{tenant}:{session_id}"

    def get(self, session_id: str) -> list:
        key = self._get_session_key(session_id)
        return self.sessions.get(key, [])

    def append(self, session_id: str, user_msg: str, assistant_msg: str):
        key = self._get_session_key(session_id)
        if key not in self.sessions:
            self.sessions[key] = []
        self.sessions[key].append({"role": "user", "content": user_msg})
        self.sessions[key].append({"role": "assistant", "content": assistant_msg})
        self._save_to_file()

    def set_history(self, session_id: str, history: list):
        """直接设置某个会话的历史记录（用于切换租户时保存）"""
        if history:
            key = self._get_session_key(session_id)
            self.sessions[key] = history.copy()
            self._save_to_file()

    def get_history(self, session_id: str) -> list:
        """获取完整历史记录"""
        key = self._get_session_key(session_id)
        return self.sessions.get(key, [])

# 全局单例
memory = ConversationMemory()