class ConversationMemory:
    def __init__(self):
        # 用一个字典存储每个 session 的对话历史
        self.sessions = {}

    def get(self, session_id: str) -> list:
        """返回该会话的历史消息列表，没有则返回空列表"""
        return self.sessions.get(session_id, [])

    def append(self, session_id: str, user_msg: str, assistant_msg: str):
        """在会话历史末尾添加一轮对话"""
        if session_id not in self.sessions:
            self.sessions[session_id] = []
        self.sessions[session_id].append({"role": "user", "content": user_msg})
        self.sessions[session_id].append({"role": "assistant", "content": assistant_msg})

# 创建一个全局实例，方便其他模块导入
memory = ConversationMemory()