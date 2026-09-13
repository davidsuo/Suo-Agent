# scripts/diagnose_rag.py
"""
RAGV2 检索诊断脚本
本地直接调用 search_knowledge_v2，绕过 FastAPI，看检索细节。
用法：python scripts/diagnose_rag.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.rag_v2 import search_knowledge_v2

QUERIES = [
    "2024年10月份的咖啡销售收入是多少？",
    "HP打印机报错0x80004005怎么处理",
    "公司下午茶的报销流程是怎样的",  # 负样本
]

for q in QUERIES:
    print(f"\n{'='*70}")
    print(f"Query: {q}")
    print('='*70)
    result = search_knowledge_v2(q, '')
    print(f"context_text 长度: {len(result['context_text'])}")
    print(f"sources 数: {len(result['sources'])}")
    for i, s in enumerate(result['sources']):
        print(f"  [{i+1}] score={s['score']} | doc_id={s.get('doc_id')} | file={s['file_name']} | dept={s['department']}")
    if result['context_text']:
        preview = result['context_text'][:300].replace('\n', ' | ')
        print(f"context_text 前 300 字: {preview}")