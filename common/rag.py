# common/rag.py
import os
import json
import uuid
import datetime
import re
from typing import List

RAG_DATA_FILE = os.path.join(os.getcwd(), "rag_data.json")

def _load_store() -> dict:
    if os.path.exists(RAG_DATA_FILE):
        try:
            with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_store(store: dict):
    with open(RAG_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

def _chunk_text(text: str, chunk_size=2000) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i:i+chunk_size])
    return chunks

def index_document(file_path: str, session_id: str, tags: str = "") -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        chunks = _chunk_text(content)
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

def search_knowledge(query: str, session_id: str, tags: str = "") -> str:
    """增强版检索：精确提取用户提到的月份数据，极大地提高计算准确率"""
    store = _load_store()
    if session_id not in store:
        return ""
    
    docs = store[session_id]
    
    # 1. 提取问题中提到的年份和月份（如2024年4月 -> 2024/4）
    year_match = re.search(r'(20\d{2})年', query)
    month_match = re.search(r'(\d{1,2})月份', query)
    
    target_prefix = ""
    if year_match and month_match:
        target_prefix = f"{year_match.group(1)}/{int(month_match.group(1))}/"
    
    # 2. 如果明确指定了年月，直接精确提取该年月所有数据
    if target_prefix:
        matched_texts = []
        for doc in docs:
            if target_prefix in doc.get("text", ""):
                matched_texts.append(doc.get("text", ""))
        if matched_texts:
            print(f"[RAG] 已精确提取 {target_prefix} 的数据")
            return "\n\n".join(matched_texts)
    
    # 3. 如果没指定月份，回退到原有的关键词匹配逻辑
    clean_query = query.replace("，", " ").replace("。", " ").replace("？", " ").replace("?", " ").replace(" ", "")
    grams = set()
    for i in range(len(clean_query)):
        for j in range(i + 2, min(i + 6, len(clean_query) + 1)):
            grams.add(clean_query[i:j])
    
    matched = []
    for doc in docs:
        text = doc.get("text", "")
        score = 0
        for gram in grams:
            if gram in text:
                score += 1
        if score >= 3:
            matched.append(text)
    
    if matched:
        return "\n\n".join(list(dict.fromkeys(matched))[:5])
    
    return ""