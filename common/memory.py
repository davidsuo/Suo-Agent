# common/memory.py
import json
import os
import uuid
from typing import List, Dict, Optional

MEMORY_FILE = os.path.join(os.getcwd(), "memory.json")

class ConversationMemory:
    def __init__(self):
        self.memory_store = {}  # 存储会话记忆
        self.all_tenants = set()  # 存储所有租户
        self.current_user = None
        self._load()

    def _load(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.memory_store = data.get("store", {})
                    self.all_tenants = set(data.get("tenants", []))
            except Exception:
                pass

    def _save(self):
        data = {"store": self.memory_store, "tenants": list(self.all_tenants)}
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def set_tenant(self, session_id: str, tenant: str):
        self.all_tenants.add(tenant)
        self._save()

    def get_tenant(self, session_id: str) -> str:
        for s_id, data in self.memory_store.items():
            if s_id == session_id:
                return data.get("tenant", "default")
        return "default"

    def set_current_user(self, user: Optional[dict]):
        self.current_user = user

    def append(self, session_id: str, user_msg: str, assistant_msg: str):
        if session_id not in self.memory_store:
            self.memory_store[session_id] = {"history": [], "tenant": "default", "files": {}, "file_context": ""}
        self.memory_store[session_id]["history"].append({"role": "user", "content": user_msg})
        self.memory_store[session_id]["history"].append({"role": "assistant", "content": assistant_msg})
        self._save()

    def get(self, session_id: str) -> List[dict]:
        if session_id in self.memory_store:
            return self.memory_store[session_id]["history"]
        return []

    def get_history(self, session_id: str) -> List[dict]:
        return self.get(session_id)

    def set_file_context(self, session_id: str, context: str):
        if session_id not in self.memory_store:
            self.memory_store[session_id] = {"history": [], "tenant": "default", "files": {}, "file_context": ""}
        self.memory_store[session_id]["file_context"] = context
        self._save()

    def get_file_context(self, session_id: str) -> str:
        if session_id in self.memory_store:
            return self.memory_store[session_id].get("file_context", "")
        return ""

    def add_uploaded_file(self, session_id: str, filename: str, content: str):
        if session_id not in self.memory_store:
            self.memory_store[session_id] = {"history": [], "tenant": "default", "files": {}, "file_context": ""}
        self.memory_store[session_id]["files"][filename] = content
        self._save()

    def get_uploaded_file_names(self, session_id: str) -> List[str]:
        if session_id in self.memory_store:
            return list(self.memory_store[session_id].get("files", {}).keys())
        return []

    def get_uploaded_file_content(self, session_id: str, filename: str) -> str:
        if session_id in self.memory_store:
            return self.memory_store[session_id].get("files", {}).get(filename, "")
        return ""

    def get_all_projects(self, username: str) -> List[str]:
        """安全获取某用户的所有项目名（不依赖内部属性）"""
        projects = ["主对话"]
        for key in self.memory_store.keys():
            if key.startswith(f"{username}_"):
                proj = key.split("_", 1)[1]
                if proj != "主对话":
                    projects.append(proj)
        # 去重并保持顺序
        seen = set()
        unique_projects = []
        for p in projects:
            if p not in seen:
                unique_projects.append(p)
                seen.add(p)
        return unique_projects

memory = ConversationMemory()