# common/long_term_memory.py
"""
长期记忆模块（US-17）

职责：
1. 结构化长期记忆：用户偏好 / 历史决策 / 上下文图谱
2. 三类数据：preferences / decisions / graph
3. 三个隐私级别：user / team / global
4. 工具化接口：供 LLM 主动读写

设计原则：
- 物理层只读，不自动写。写入必须 LLM 显式调用工具。
- 读取时按 user → team → global 逐级聚合。
- graph 节点由 orchestrator 在会话开始时同步。
"""
import json
import os
import uuid
import datetime
from typing import List
from zoneinfo import ZoneInfo

UPLOAD_DIR = os.getenv(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
)
os.makedirs(UPLOAD_DIR, exist_ok=True)
LTM_FILE = os.path.join(UPLOAD_DIR, "memory_long_term.json")

CURRENT_SCHEMA_V = 1
MAX_PREFS_PER_SCOPE = 50
MAX_DECISIONS_PER_USER = 100
MAX_EDGES = 1000


def _now() -> str:
    return datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


class LongTermMemory:
    def __init__(self):
        self.store = self._empty_store()
        self._load()

    def _empty_store(self) -> dict:
        return {
            "schema_v": CURRENT_SCHEMA_V,
            "users": {},
            "teams": {},
            "global": {"preferences": []},
            "graph": {"nodes": {}, "edges": []},
        }

    def _load(self):
        if not os.path.exists(LTM_FILE):
            return
        try:
            with open(LTM_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in self._empty_store().items():
                    data.setdefault(k, v)
                self.store = data
        except Exception as e:
            print(f"###long_term_memory### 加载失败，使用空 store: {e}")
            self.store = self._empty_store()

    def _save(self):
        try:
            with open(LTM_FILE, "w", encoding="utf-8") as f:
                json.dump(self.store, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"###long_term_memory### 保存失败: {e}")

    # ==================== 偏好 ====================
    def remember_preference(self, username: str, key: str, value: str,
                            scope: str = "user", department: str = "") -> dict:
        key = (key or "").strip()[:60]
        value = (value or "").strip()[:300]
        if not key or not value:
            return {"ok": False, "msg": "key 和 value 不能为空"}

        item = {
            "id": f"pref_{uuid.uuid4().hex[:8]}",
            "key": key,
            "value": value,
            "scope": scope,
            "updated_at": _now(),
        }

        if scope == "user":
            if not username:
                return {"ok": False, "msg": "scope=user 需要 username"}
            prefs = self.store["users"].setdefault(
                username, {"preferences": [], "decisions": []}
            )["preferences"]
        elif scope == "team":
            if not department:
                return {"ok": False, "msg": "scope=team 需要 department 参数"}
            prefs = self.store["teams"].setdefault(department, {"preferences": []})["preferences"]
        elif scope == "global":
            prefs = self.store["global"]["preferences"]
        else:
            return {"ok": False, "msg": f"不支持的 scope: {scope}"}

        # 同 key 覆盖（去重）
        for i, p in enumerate(prefs):
            if p.get("key") == key:
                prefs[i] = item
                self._save()
                return {"ok": True, "id": item["id"], "action": "updated"}

        if len(prefs) >= MAX_PREFS_PER_SCOPE:
            prefs.pop(0)
        prefs.append(item)
        self._save()
        return {"ok": True, "id": item["id"], "action": "created"}

    def recall_preference(self, username: str, department: str = "",
                          scope: str = "", key: str = "") -> List[dict]:
        results = []
        if (not scope or scope == "user") and username:
            for p in self.store["users"].get(username, {}).get("preferences", []):
                if not key or key in p.get("key", ""):
                    results.append({**p, "_scope": "user"})
        if (not scope or scope == "team") and department:
            for p in self.store["teams"].get(department, {}).get("preferences", []):
                if not key or key in p.get("key", ""):
                    results.append({**p, "_scope": "team"})
        if not scope or scope == "global":
            for p in self.store["global"].get("preferences", []):
                if not key or key in p.get("key", ""):
                    results.append({**p, "_scope": "global"})
        return results

    # ==================== 决策 ====================
    def remember_decision(self, username: str, topic: str, choice: str,
                          reason: str = "") -> dict:
        topic = (topic or "").strip()[:100]
        choice = (choice or "").strip()[:200]
        if not topic or not choice:
            return {"ok": False, "msg": "topic 和 choice 不能为空"}
        if not username:
            return {"ok": False, "msg": "缺少 username"}
        decisions = self.store["users"].setdefault(
            username, {"preferences": [], "decisions": []}
        )["decisions"]
        item = {
            "id": f"dec_{uuid.uuid4().hex[:8]}",
            "topic": topic,
            "choice": choice,
            "reason": (reason or "").strip()[:300],
            "created_at": _now(),
        }
        if len(decisions) >= MAX_DECISIONS_PER_USER:
            decisions.pop(0)
        decisions.append(item)
        self._save()
        return {"ok": True, "id": item["id"]}

    def recall_decision(self, username: str, topic: str = "") -> List[dict]:
        if not username:
            return []
        results = []
        for d in self.store["users"].get(username, {}).get("decisions", []):
            if not topic or topic in d.get("topic", ""):
                results.append(d)
        return results

    # ==================== 图谱 ====================
    def _ensure_node(self, username: str) -> dict:
        nodes = self.store["graph"]["nodes"]
        if username not in nodes:
            nodes[username] = {"type": "user", "created_at": _now()}
        return nodes[username]

    def sync_user_node(self, username: str, department: str = "", real_name: str = ""):
        """会话开始时由 orchestrator 调用，同步用户元数据到 graph 节点"""
        if not username:
            return
        node = self._ensure_node(username)
        changed = False
        if department and node.get("department") != department:
            node["department"] = department
            changed = True
        if real_name and node.get("real_name") != real_name:
            node["real_name"] = real_name
            changed = True
        if changed:
            self._save()

    def remember_relation(self, from_user: str, to_user: str,
                          relation_type: str = "collaborates_with") -> dict:
        if not from_user or not to_user or from_user == to_user:
            return {"ok": False, "msg": "from_user / to_user 无效"}
        self._ensure_node(from_user)
        self._ensure_node(to_user)
        edges = self.store["graph"]["edges"]
        for e in edges:
            if (e.get("from") == from_user and e.get("to") == to_user
                    and e.get("type") == relation_type):
                e["weight"] = int(e.get("weight", 1)) + 1
                e["updated_at"] = _now()
                self._save()
                return {"ok": True, "edge_id": e.get("id"),
                        "action": "updated", "weight": e["weight"]}
        edge = {
            "id": f"edge_{uuid.uuid4().hex[:8]}",
            "from": from_user,
            "to": to_user,
            "type": relation_type,
            "weight": 1,
            "updated_at": _now(),
        }
        if len(edges) >= MAX_EDGES:
            edges.pop(0)
        edges.append(edge)
        self._save()
        return {"ok": True, "edge_id": edge["id"], "action": "created", "weight": 1}

    def recall_relations(self, username: str = "") -> List[dict]:
        results = []
        for e in self.store["graph"]["edges"]:
            if (not username) or e.get("from") == username or e.get("to") == username:
                results.append(e)
        return results

    # ==================== 物理层上下文构建 ====================
    def build_context(self, username: str, department: str = "") -> str:
        """物理层：主循环前，聚合当前用户的长期记忆，生成注入文本"""
        lines = []

        # 1. 用户偏好
        user_prefs = self.recall_preference(username, department, scope="user")
        for p in user_prefs[-5:]:
            lines.append(f"- [用户偏好] {p['key']}: {p['value']}")

        # 2. 团队偏好
        if department:
            team_prefs = self.recall_preference(username, department, scope="team")
            for p in team_prefs[-5:]:
                lines.append(f"- [团队偏好/{department}] {p['key']}: {p['value']}")

        # 3. 全局偏好
        for p in self.recall_preference(username, department, scope="global")[-3:]:
            lines.append(f"- [全局偏好] {p['key']}: {p['value']}")

        # 4. 近期决策
        for d in self.recall_decision(username)[-3:]:
            reason_part = f"（原因：{d['reason']}）" if d.get("reason") else ""
            lines.append(f"- [历史决策] {d['topic']} → {d['choice']}{reason_part}")

        # 5. 相关关系
        relations = self.recall_relations(username)
        if relations:
            top = sorted(relations, key=lambda e: -int(e.get("weight", 1)))[:3]
            for e in top:
                other = e["to"] if e["from"] == username else e["from"]
                rel_cn = {"collaborates_with": "经常协作", "reports_to": "汇报关系"}.get(
                    e["type"], e["type"])
                lines.append(f"- [相关同事] 与 {other} {rel_cn}（{e['weight']} 次）")

        if not lines:
            return ""
        return "【用户长期记忆】\n" + "\n".join(lines)


long_term_memory = LongTermMemory()