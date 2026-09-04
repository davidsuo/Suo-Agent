# common/rag_v2.py
import os
import json
import uuid
import datetime
from typing import List

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    print("❌ 请先安装依赖: pip install chromadb")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DIR = os.path.join(BASE_DIR, "chroma_db")
RAG_DATA_FILE = os.path.join(BASE_DIR, "rag_data.json")

# 初始化 ChromaDB 客户端（持久化模式）
_client = chromadb.PersistentClient(path=CHROMA_DIR)
_collection = _client.get_or_create_collection(name="enterprise_kb")

# 简单的文档感知分块
def smart_chunk_text(text: str, max_chunk_size: int = 1500) -> List[str]:
    if len(text) <= max_chunk_size:
        return [text]
    paragraphs = text.split("\n")
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        if len(current_chunk) + len(para) > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = para + "\n"
        else:
            current_chunk += para + "\n"
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def index_document_v2(file_path: str, tags: str = ""):
    """新的索引入库逻辑：读取 -> 智能分块 -> 向量化入库 -> 同步Json列表与内容"""
    try:
        import pandas as pd
        ext = os.path.splitext(file_path)[1].lower()
        content = ""
        if ext in [".txt", ".md"]:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        elif ext in [".csv", ".xlsx", ".xls"]:
            if ext == ".csv":
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            df = df.fillna("")
            content = df.to_csv(index=False)
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(file_path)
                for page in reader.pages:
                    content += page.extract_text() + "\n"
            except ImportError:
                return "❌ 缺少 pypdf 库"
        elif ext == ".docx":
            try:
                from docx import Document
                doc = Document(file_path)
                for para in doc.paragraphs:
                    content += para.text + "\n"
                for table in doc.tables:
                    for row in table.rows:
                        row_text = [cell.text for cell in row.cells]
                        content += " | ".join(row_text) + "\n"
            except ImportError:
                return "❌ 缺少 python-docx 库"
        else:
            return f"❌ 不支持的文件格式: {ext}"

        if not content.strip():
            return "❌ 文件内容为空。"

        chunks = smart_chunk_text(content)
        file_name = os.path.basename(file_path)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 向ChromaDB写入向量
        ids = [str(uuid.uuid4()) for _ in chunks]
        metadatas = [{"file_name": file_name, "tags": tags, "time": current_time} for _ in chunks]
        _collection.add(
            documents=chunks,
            metadatas=metadatas,
            ids=ids
        )

        # 【核心修复】同步更新 rag_data.json，不仅更新files，还要更新store，保证V1模式也能完美检索！
        store = {"files": [], "store": {}}
        if os.path.exists(RAG_DATA_FILE):
            try:
                with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                store = {"files": [], "store": {}}

        if not any(f.get("file_name") == file_name for f in store.get("files", [])):
            store.setdefault("files", []).append({
                "file_name": file_name,
                "tags": tags if tags else "(无)",
                "created_at": current_time,
                "chunks": len(chunks)
            })

        target_key = f"tag_{tags.strip()}" if tags and tags.strip() else "__global__"
        if target_key not in store["store"]:
            store["store"][target_key] = []
        for chunk in chunks:
            store["store"][target_key].append({
                "id": str(uuid.uuid4()),
                "text": chunk,
                "tags": tags,
                "file_name": file_name,
                "time": current_time
            })

        with open(RAG_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

        return f"✅ [V2] 文档已成功入库到ChromaDB（共 {len(chunks)} 个智能分块）。"
    except Exception as e:
        return f"❌ [V2] 文档处理失败: {e}"

def search_knowledge_v2(query: str, tags: str = ""):
    """【V2】混合检索 + 元数据过滤：精确关键词 + 向量语义 + 标签过滤"""
    try:
        import re as _re
        _year_match = _re.search(r'(20\d{2})', query)
        _month_match = _re.search(r'(\d{1,2})月份', query)
        target_prefix = ""
        if _year_match and _month_match:
            target_prefix = f"{_year_match.group(1)}/{int(_month_match.group(1))}/"

        where_filter = None
        if tags and tags.strip():
            where_filter = {"tags": tags.strip()}

        # 元数据过滤 + 精确匹配模式
        all_data = _collection.get(
            include=["documents", "metadatas"],
            where=where_filter
        )
        
        matched_texts = []
        if target_prefix:
            for doc_text in all_data['documents']:
                if target_prefix in doc_text:
                    matched_texts.append(doc_text)
        else:
            # 语义匹配模式
            results = _collection.query(
                query_texts=[query],
                n_results=5,
                where=where_filter
            )
            if results and results['documents']:
                matched_texts = results['documents'][0][:5]

        if matched_texts:
            return "\n\n".join(matched_texts[:10])[:20000]
        return ""
    except Exception as e:
        print(f"###DEBUG### V2混合检索失败: {e}")
        return ""