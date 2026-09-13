# scripts/list_collections.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.rag_v2 import _chroma_client, _list_all_collections

print("=== 所有 dept_ 开头的 collections ===")
names = _list_all_collections()
for n in names:
    col = _chroma_client.get_collection(name=n)
    count = col.count()
    print(f"  {n}: {count} 条")

print("\n=== 所有 collections（含非 dept_） ===")
all_cols = _chroma_client.list_collections()
for c in all_cols:
    name = c if isinstance(c, str) else c.name
    try:
        col = _chroma_client.get_collection(name=name)
        print(f"  {name}: {col.count()} 条")
    except Exception as e:
        print(f"  {name}: 查询失败 {e}")