# common/rag.py
import os
import json
import uuid
from typing import List
import datetime

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
    """索引文档（纯文本存储，不依赖模型，极速稳定）"""
    try:
        # 限制文件读取大小（最多读前50000字符，防止内存溢出）
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(50000)
        
        # 切片
        chunks = _chunk_text(content)
        
        # 获取全局存储
        store = _load_store()
        
        # 为该 session 创建列表
        if session_id not in store:
            store[session_id] = []
        
        # 保存分块数据
        for chunk in chunks:
            store[session_id].append({
                "id": str(uuid.uuid4()),
                "text": chunk,
                "tags": tags,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        
        _save_store(store)
        return f"✅ 文档已成功索引（共 {len(chunks)} 个片段，纯文本模式，无需下载模型）。"
    except Exception as e:
        return f"❌ 文档处理失败: {e}"

def search_knowledge(query: str, session_id: str, tags: str = "") -> str:
    """检索知识（简单关键词匹配，绝对稳定）"""
    store = _load_store()
    
    if session_id not in store:
        return ""
    
    docs = store[session_id]
    
    # 简单匹配：如果标签匹配或关键词出现在文本中，就返回
    matched_texts = []
    for doc in docs:
        # 过滤标签
        if tags and tags not in doc.get("tags", ""):
            continue
        
        text = doc.get("text", "")
        # 简单分词，只要有一半关键词命中就返回
        query_parts = query.replace("？", "").replace("?", "").split()
        for part in query_parts:
            if part and part in text:
                matched_texts.append(text)
                break
    
    # 最多取3个最相关的
    if matched_texts:
        return "\n\n".join(matched_texts[:3])
    return ""