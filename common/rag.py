# common/rag.py
import os
import json
import uuid
import datetime
from typing import List

# 存储路径
RAG_DATA_FILE = os.path.join(os.getcwd(), "rag_data.json")

def _load_store() -> dict:
    """加载本地知识库存储（JSON）"""
    if os.path.exists(RAG_DATA_FILE):
        try:
            with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_store(store: dict):
    """保存知识库存储"""
    with open(RAG_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

def _chunk_text(text: str, chunk_size=500) -> List[str]:
    """简单的文本切片"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i:i+chunk_size])
    return chunks
   

def index_document(file_path: str, session_id: str, tags: str = "") -> str:
    try:
        # ✅ 修改1：读取完整文件，绝不截断数据
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # ✅ 修改2：调整切分大小，从500改为2000，确保多天数据不被切碎
        chunks = _chunk_text(content, chunk_size=2000)
        
        # 后续保存逻辑不变...
        store = _load_store()
        if session_id not in store:
            store[session_id] = []
        for chunk in chunks:
            store[session_id].append({
                "id": str(uuid.uuid4()),
                "text": chunk,
                "tags": tags,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        _save_store(store)
        return f"✅ 文档已成功索引（共 {len(chunks)} 个片段，已完整读取）。"
    except Exception as e:
        return f"❌ 文档处理失败: {e}"

def search_knowledge(query, session_id, tags="", top_k=5):
    """检索知识（跨项目搜索，并严格遵守 top_k 参数）"""
    store = _load_store()
    
    # 1. 提取当前用户名（如 alice）
    username = session_id.split('_')[0] if '_' in session_id else session_id
    # 2. 找出该用户下所有项目的知识库
    candidate_sessions = [k for k in store.keys() if k.startswith(username + "_")]
    if session_id in store:
        candidate_sessions.insert(0, session_id)  # 优先检索当前项目

    combined_docs = []
    for s_id in candidate_sessions:
        combined_docs.extend(store.get(s_id, []))
    
    if not combined_docs:
        return ""
    
    # 基础分词
    import re
    clean_query = query.replace("，", " ").replace("。", " ").replace("？", " ").replace("?", " ").replace(" ", "")
    grams = set()
    for i in range(len(clean_query)):
        for j in range(i + 2, min(i + 6, len(clean_query) + 1)):
            grams.add(clean_query[i:j])
    
    matched = []
    for doc in combined_docs:
        text = doc.get("text", "")
        score = 0
        for gram in grams:
            if gram in text:
                score += 1
        if score >= 3:
            matched.append(text)
    
    if matched:
        return "\n\n".join(list(dict.fromkeys(matched))[:top_k])  # ✅ 改为使用 top_k
    
    # 兜底关键词
    keywords = ["销售", "收入", "价格", "coffee", "2024", "数据"]
    for kw in keywords:
        if kw in query:
            for doc in combined_docs:
                if kw in doc.get("text", ""):
                    matched.append(doc.get("text", ""))
            if matched:
                return "\n\n".join(list(dict.fromkeys(matched))[:top_k])  # ✅ 改为使用 top_k
                
    return "