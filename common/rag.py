# common/rag.py
import os
import uuid
import datetime  # ✅ 新增：用于记录上传时间
import chromadb
from typing import List
import hashlib

PERSIST_DIR = os.path.join(os.getcwd(), "rag_data")
client = chromadb.PersistentClient(path=PERSIST_DIR)

def _get_collection(session_id: str):
    # ✅ 终极修复：使用 MD5 哈希将中文/特殊字符转换成纯英文名，规避 ChromaDB 的名称限制
    hash_object = hashlib.md5(session_id.encode())
    safe_name = hash_object.hexdigest()[:20]  # 取前20位数字字母，绝对安全
    collection_name = f"kb_{safe_name}"
    return client.get_or_create_collection(name=collection_name)

def _chunk_text(text: str, chunk_size=500, overlap=50) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks

# ✅ 核心升级：支持传入元数据标签
def index_document(file_path: str, session_id: str, tags: str = "") -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        collection = _get_collection(session_id)
        chunks = _chunk_text(content)
        ids = [str(uuid.uuid4()) for _ in chunks]
        
        # ✅ 新增：构建丰富的元数据字典
        metadatas = []
        for _ in chunks:
            metadatas.append({
                "session_id": session_id,
                "tags": tags,  # 用户自定义标签（逗号分隔）
                "doc_type": os.path.splitext(file_path)[1].lower().lstrip('.'),
                "upload_time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        
        collection.add(ids=ids, documents=chunks, metadatas=metadatas)
        return f"✅ 文档已存入知识库（含元数据），共 {len(chunks)} 个片段。上传时间：{metadatas[0]['upload_time']}"
    except Exception as e:
        return f"❌ 文档处理失败: {e}"

# ✅ 核心升级：支持基于元数据的过滤检索
def search_knowledge(query: str, session_id: str, tags: str = "", top_k: int = 3) -> str:
    try:
        collection = _get_collection(session_id)
        if collection.count() == 0:
            return ""
        
        # 构建过滤条件（必须包含当前项目隔离）
        where_clause = {"session_id": session_id}
        if tags:
            # 如果传入了标签，严格匹配标签
            where_clause["tags"] = tags
        
        results = collection.query(
            query_texts=[query], 
            n_results=min(top_k, collection.count()), 
            where=where_clause  # ✅ 使用元数据过滤
        )
        
        if results and results["documents"]:
            docs = results["documents"][0]
            return "\n".join(docs)
        return ""
    except Exception:
        return ""