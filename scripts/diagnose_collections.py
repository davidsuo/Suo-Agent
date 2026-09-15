# scripts/diagnose_collections.py
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.rag_v2 import _chroma_client, _list_all_collections

print("=" * 60)
print("【1】ChromaDB 里的所有 collections")
print("=" * 60)
for name in _list_all_collections():
    try:
        col = _chroma_client.get_collection(name=name)
        count = col.count()
        print(f"  {name}: {count} 条")
        # 打印前 2 条元数据
        data = col.get(limit=2, include=["metadatas", "documents"])
        for i, meta in enumerate(data.get("metadatas", [])[:2]):
            doc_preview = data["documents"][i][:60] if data.get("documents") else ""
            print(f"    [{i}] metadata={meta}")
            print(f"         doc_preview={doc_preview!r}")
    except Exception as e:
        print(f"  {name}: 查询失败 {e}")

print()
print("=" * 60)
print("【2】rag_data.json 里的 files 及 tags")
print("=" * 60)
with open("rag_data.json", "r", encoding="utf-8") as f:
    store = json.load(f)
for item in store.get("files", []):
    print(f"  {item.get('file_name')}: tags={item.get('tags')!r}, dept={item.get('department')}")

print()
print("=" * 60)
print("【3】rag_data.json 里的 store key")
print("=" * 60)
for key, docs in store.get("store", {}).items():
    print(f"  {key}: {len(docs)} 条")