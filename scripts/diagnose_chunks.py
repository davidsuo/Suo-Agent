# scripts/diagnose_chunks.py
"""
深度诊断：
1. 检查 rag_data.json 里 coffee_sales.csv 的所有块（月度汇总是否生成？）
2. 检查向量路对所有块的完整排名（月度汇总排第几？）
"""
import os
import sys
import json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ========== 1. 看 rag_data.json ==========
print("=" * 70)
print("【1】rag_data.json 里 coffee_sales.csv 的所有块")
print("=" * 70)
with open('rag_data.json', 'r', encoding='utf-8') as f:
    store = json.load(f)

csv_docs = []
for key, docs in store.get('store', {}).items():
    for doc in docs:
        if doc.get('file_name') == 'coffee_sales.csv':
            csv_docs.append(doc)

print(f"总块数: {len(csv_docs)}")
for i, doc in enumerate(csv_docs):
    preview = doc['text'][:80].replace('\n', ' | ')
    print(f"  [{i+1:2d}] id={doc['id'][:8]} | {preview}")

# ========== 2. 向量路完整排名 ==========
print()
print("=" * 70)
print("【2】向量路 Top 20（看月度汇总排第几）")
print("=" * 70)

from common.rag_v2 import (
    _vector_model, _chroma_client, _list_all_collections,
    _bm25_index, _bm25_docs, _tokenize,
)

query = "2024年10月份的咖啡销售收入是多少？"

# 向量路
query_emb = _vector_model.encode([query], normalize_embeddings=True).tolist()
all_hits = []
for cname in _list_all_collections():
    col = _chroma_client.get_collection(name=cname)
    res = col.query(
        query_embeddings=query_emb,
        n_results=50,
        include=["documents", "metadatas", "distances"],
    )
    if res and res["ids"] and res["ids"][0]:
        for i, doc_id in enumerate(res["ids"][0]):
            sim = 1.0 - res["distances"][0][i]
            all_hits.append((sim, doc_id, res["documents"][0][i]))

all_hits.sort(key=lambda x: x[0], reverse=True)
print(f"总候选数: {len(all_hits)}")
for i, (sim, doc_id, text) in enumerate(all_hits[:20]):
    preview = text[:60].replace('\n', ' | ')
    print(f"  [{i+1:2d}] sim={sim:.4f} | id={doc_id[:8]} | {preview}")

# ========== 3. BM25 完整排名 ==========
print()
print("=" * 70)
print("【3】BM25 路 Top 20")
print("=" * 70)

scores = _bm25_index.get_scores(_tokenize(query))
top_indices = np.argsort(scores)[::-1][:20]
for i, idx in enumerate(top_indices):
    if scores[idx] > 0:
        doc = _bm25_docs[idx]
        preview = doc['text'][:60].replace('\n', ' | ')
        print(f"  [{i+1:2d}] score={scores[idx]:.4f} | id={doc['id'][:8]} | file={doc['file_name']} | {preview}")