# scripts/diagnose_retrieval.py
import os
import sys
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 1. 检查 rag_data.json 里 CSV 的块
print("=" * 70)
print("【1】rag_data.json 里有哪些文档的块")
print("=" * 70)
with open('rag_data.json', 'r', encoding='utf-8') as f:
    store = json.load(f)

for key, docs in store.get('store', {}).items():
    file_names = set(d.get('file_name', '') for d in docs)
    print(f"  {key}: {len(docs)} 块 → 文件: {file_names}")

# 2. 检查 BM25 索引状态
print()
print("=" * 70)
print("【2】BM25 索引里包含哪些块")
print("=" * 70)
from common.rag_v2 import _bm25_index, _bm25_docs, _tokenize, search_knowledge_v2

if _bm25_index is None:
    print("  ⚠️ BM25 索引为空！")
else:
    print(f"  BM25 索引文档数: {len(_bm25_docs)}")
    file_names = set(d.get('file_name', '') for d in _bm25_docs)
    print(f"  包含文件: {file_names}")

# 3. 直接调用检索，看命中什么
print()
print("=" * 70)
print("【3】直接调用 search_knowledge_v2")
print("=" * 70)
query = "2024年10月份的咖啡销售收入是多少？"
result = search_knowledge_v2(query, '')

print(f"  context_text 长度: {len(result['context_text'])}")
print(f"  sources 数: {len(result['sources'])}")
for s in result['sources']:
    print(f"    score={s['score']} | file={s['file_name']} | dept={s['department']}")

if result['context_text']:
    print(f"\n  context_text 前 500 字：")
    print(result['context_text'][:500])