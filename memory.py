# memory.py
class ConversationMemory:
    def __init__(self):
        self.sessions = {}
        self.tenant_map = {}
        self.all_tenants = set()          # 确保为集合
        self.all_tenants.add("default")   # 默认包含 default

    def set_tenant(self, session_id, tenant_id):
        self.tenant_map[session_id] = tenant_id
        self.all_tenants.add(str(tenant_id))   # 转为字符串存储

    def get_tenant(self, session_id):
        return self.tenant_map.get(session_id, "default")

    # 其他方法保持不变...  

    def _get_session_key(self, session_id):
        tenant = self.tenant_map.get(session_id, "default")
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
    
    def set_history(self, session_id: str, history: list):
        """直接设置某个会话的历史记录（用于切换租户时保存当前窗口）"""
        if history:
            key = self._get_session_key(session_id)
            self.sessions[key] = history.copy()

    def get_history(self, session_id: str) -> list:
        """获取完整历史记录，若不存在则返回空列表"""
        key = self._get_session_key(session_id)
        return self.sessions.get(key, [])

# 模块级单例，供其他模块导入
memory = ConversationMemory()