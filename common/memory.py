# common/memory.py
import json
import os
import uuid
from typing import List, Dict, Optional

# 优先使用持久化磁盘路径
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads'))
os.makedirs(UPLOAD_DIR, exist_ok=True)
MEMORY_FILE = os.path.join(UPLOAD_DIR, "memory.json")

# ==================== 【US-08】Memory Schema 版本控制 ====================
# 版本升级日志：
#   v1（隐式）：无 schema_v 字段，初始版本
#   v2：引入 schema_v 字段，加载时自动清旧 history
# 未来升级：改 CURRENT_SCHEMA_V，并在 _migrate 里补对应的迁移分支
CURRENT_SCHEMA_V = 2


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
                    raw_store = data.get("store", {})
                    self.memory_store = self._migrate(raw_store)
                    self.all_tenants = set(data.get("tenants", []))
            except Exception as e:
                print(f"###memory### 加载失败，将使用空 memory: {e}")
                self.memory_store = {}
        else:
            self.memory_store = {}

    def _migrate(self, raw_store: dict) -> dict:
        """按 schema_v 迁移旧 memory 数据（US-08）。

        规则：
        - 无 schema_v 或 schema_v < CURRENT_SCHEMA_V → 清空 history，保留 files/tenant
        - schema_v == CURRENT_SCHEMA_V → 原样保留
        """
        migrated = {}
        cleaned_count = 0
        for sid, data in raw_store.items():
            if not isinstance(data, dict):
                continue
            ver = data.get("schema_v", 1)   # 无字段视为 v1
            if ver < CURRENT_SCHEMA_V:
                data["history"] = []
                data["schema_v"] = CURRENT_SCHEMA_V
                cleaned_count += 1
            else:
                data["schema_v"] = ver
            migrated[sid] = data
        if cleaned_count > 0:
            print(f"###memory迁移### 清理 {cleaned_count} 个旧 session 的 history（schema < v{CURRENT_SCHEMA_V}）")
        return migrated

    def _save(self):
        data = {"store": self.memory_store, "tenants": list(self.all_tenants)}
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear(self, session_id: str = None):
        """
        清空 memory。

        - 传 session_id：只清空该 session 的 history
        - 不传：清空所有 session 的 history（保留 files 和 tenant 元数据）
        """
        if session_id:
            if session_id in self.memory_store:
                self.memory_store[session_id]["history"] = []
                print(f"✅ 已清空 {session_id} 的 history")
        else:
            for sid in self.memory_store.keys():
                self.memory_store[sid]["history"] = []
            print(f"✅ 已清空所有 session 的 history")
        self._save()

    def set_tenant(self, session_id: str, tenant: str):
        self.all_tenants.add(tenant)
        self._save()

    def get_tenant(self, session_id: str) -> str:
        """从 session_id 提取租户（用户名），实现用户间日程隔离。"""
        if "_" in session_id:
            return session_id.split("_", 1)[0]
        return session_id

    def set_current_user(self, user: Optional[dict]):
        self.current_user = user

    def append(self, session_id: str, user_msg: str, assistant_msg: str):
        if session_id not in self.memory_store:
            self.memory_store[session_id] = {
                "history": [], "tenant": "default",
                "files": {}, "file_context": "",
                "schema_v": CURRENT_SCHEMA_V,
            }
        # 【US-08 防御】补齐 schema_v（防止外部写入绕过 __init__ 迁移）
        self.memory_store[session_id].setdefault("schema_v", CURRENT_SCHEMA_V)
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
            self.memory_store[session_id] = {
                "history": [], "tenant": "default",
                "files": {}, "file_context": "",
                "schema_v": CURRENT_SCHEMA_V,
            }
        self.memory_store[session_id].setdefault("schema_v", CURRENT_SCHEMA_V)
        self.memory_store[session_id]["file_context"] = context
        self._save()

    def get_file_context(self, session_id: str) -> str:
        if session_id in self.memory_store:
            return self.memory_store[session_id].get("file_context", "")
        return ""

    def add_uploaded_file(self, session_id: str, filename: str, content: str):
        if session_id not in self.memory_store:
            self.memory_store[session_id] = {
                "history": [], "tenant": "default",
                "files": {}, "file_context": "",
                "schema_v": CURRENT_SCHEMA_V,
            }
        self.memory_store[session_id].setdefault("schema_v", CURRENT_SCHEMA_V)
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
        """安全获取某用户的所有项目名"""
        projects = ["主对话"]
        for key in self.memory_store.keys():
            if key.startswith(f"{username}_"):
                proj = key.split("_", 1)[1]
                if proj != "主对话":
                    projects.append(proj)
        seen = set()
        unique_projects = []
        for p in projects:
            if p not in seen:
                unique_projects.append(p)
                seen.add(p)
        return unique_projects


memory = ConversationMemory()