# scripts/verify_persistence.py
"""
验证 ChromaDB 数据持久化：
1. 第一次打开 → 读取 collection 和 count
2. 关闭客户端 → 重新打开 → 再读一次
3. 两次一致即持久化正常
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb

CHROMA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "chroma_db"
)

def read_all(round_name: str):
    print(f"\n=== {round_name} ===")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    cols = client.list_collections()
    names = [c if isinstance(c, str) else c.name for c in cols]
    print(f"Collections: {names}")
    for name in names:
        col = client.get_collection(name=name)
        print(f"  {name}: {col.count()} 条")
    # 显式清理
    del client

# 第一次打开
read_all("第一次打开")

# 第二次打开（模拟进程重启）
read_all("第二次打开（模拟重启）")

print("\n✅ 两次输出一致 → 持久化正常")
print("❌ 第二次 count=0 或列表为空 → 持久化有 bug")