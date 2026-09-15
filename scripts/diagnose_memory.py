# scripts/diagnose_memory.py
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.memory import memory

session_id = "alice_主对话"   # 如果不对，改成你的 session id
history = memory.get_history(session_id)

print(f"=== {session_id} 的完整历史（{len(history)} 条）===\n")
for i, msg in enumerate(history):
    role = msg.get("role", "?")
    content = msg.get("content", "")
    print(f"[{i}] role={role} | 长度={len(content)}")
    print(f"    前 200 字: {content[:200]}")
    print()