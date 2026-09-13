# scripts/clear_memory.py
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.memory import memory

# 要清空的 session（多个用列表）
SESSIONS_TO_CLEAR = ["alice_主对话"]

print(f"清空前：共 {len(memory.memory_store)} 个 session")
for sid in memory.memory_store.keys():
    hist_len = len(memory.memory_store[sid].get("history", []))
    print(f"  {sid}: {hist_len} 条历史")

for sid in SESSIONS_TO_CLEAR:
    if sid in memory.memory_store:
        memory.memory_store[sid]["history"] = []
        print(f"✅ 已清空 {sid} 的 history")
    else:
        print(f"⚠️ 未找到 {sid}")

# 保存
memory._save()

print(f"\n清空后：")
for sid in memory.memory_store.keys():
    hist_len = len(memory.memory_store[sid].get("history", []))
    print(f"  {sid}: {hist_len} 条历史")