# common/rag.py
import os
import json
import uuid
import datetime
import sqlite3
import re
from typing import List

RAG_DB_FILE = os.path.join(os.getcwd(), "rag_data.db")
GLOBAL_KEY = "__global__"

def _get_conn():
    conn = sqlite3.connect(RAG_DB_FILE)
    conn.execute('''CREATE TABLE IF NOT EXISTS rag_items (
        id TEXT PRIMARY KEY,
        store_key TEXT,
        text TEXT,
        tags TEXT,
        created_at TEXT
    )''')
    # 【新增】创建文件列表存储表
    conn.execute('''CREATE TABLE IF NOT EXISTS rag_files (
        file_name TEXT,
        tags TEXT,
        created_at TEXT
    )''')
    return conn

def _load_store() -> dict:
    store = {}
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT store_key, text FROM rag_items")
        for key, text in cursor.fetchall():
            if key not in store:
                store[key] = []
            store[key].append({"text": text})
        conn.close()
    except Exception:
        pass
    return store

def _save_store(store: dict):
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rag_items")
        for key, items in store.items():
            for item in items:
                cursor.execute("INSERT INTO rag_items (id, store_key, text) VALUES (?, ?, ?)",
                               (str(uuid.uuid4()), key, item["text"]))
        conn.commit()
        conn.close()
    except Exception:
        pass

# 【新增】获取已上传文档列表的函数
def list_indexed_files():
    conn = _get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT file_name, created_at FROM rag_files ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [{"file_name": r[0], "created_at": r[1]} for r in rows]
    except Exception:
        return []
    finally:
        conn.close()

def _chunk_text(text: str, chunk_size=1500) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    lines = text.split("\n")
    chunks = []
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) > chunk_size:
            if current_chunk:
                chunks.append(current_chunk)
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

def index_document(file_path: str, session_id: str, tags: str = "") -> str:
    try:
        import pandas as pd
        
        if not isinstance(file_path, str):
            if hasattr(file_path, 'name'):
                file_path = file_path.name
            else:
                file_path = str(file_path)
                
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
            return "❌ 文件内容为空或无法解析。"

        chunks = _chunk_text(content)
        store = _load_store()

        if tags and tags.strip():
            target_key = f"tag_{tags.strip()}"
        else:
            target_key = GLOBAL_KEY

        if target_key not in store:
            store[target_key] = []
        for chunk in chunks:
            store[target_key].append({
                "id": str(uuid.uuid4()),
                "text": chunk,
                "tags": tags,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })
        _save_store(store)
        
        # 【新增】将文件名写入列表数据库
        conn = _get_conn()
        cursor = conn.cursor()
        # 只取文件名，不带路径
        file_name = os.path.basename(file_path)
        cursor.execute("INSERT INTO rag_files (file_name, tags, created_at) VALUES (?, ?, ?)",
                       (file_name, tags, datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        
        return f"✅ 文档已成功索引（共 {len(chunks)} 个片段，支持 PDF/Word/表格）。"
    except Exception as e:
        return f"❌ 文档处理失败: {e}"

def search_knowledge(query: str, session_id: str, tags: str = "") -> str:
    store = _load_store()
    combined_docs = []

    if tags and tags.strip():
        target_key = f"tag_{tags.strip()}"
        if target_key in store:
            combined_docs.extend(store[target_key])

    if GLOBAL_KEY in store:
        combined_docs.extend(store[GLOBAL_KEY])

    if not combined_docs:
        return ""

    import re as _re
    _year_match = _re.search(r'(20\d{2})年', query)
    _month_match = _re.search(r'(\d{1,2})月份', query)

    target_prefix = ""
    if _year_match and _month_match:
        target_prefix = f"{_year_match.group(1)}/{int(_month_match.group(1))}/"

    matched_texts = []
    if target_prefix:
        for doc in combined_docs:
            if target_prefix in doc.get("text", ""):
                matched_texts.append(doc.get("text", ""))
    else:
        clean_query = query.replace("，", " ").replace("。", " ").replace("？", " ").replace("?", " ").replace(" ", "")
        grams = set()
        for i in range(len(clean_query)):
            for j in range(i + 2, min(i + 6, len(clean_query) + 1)):
                grams.add(clean_query[i:j])
        for doc in combined_docs:
            text = doc.get("text", "")
            score = 0
            for gram in grams:
                if gram in text:
                    score += 1
            if score >= 3:
                matched_texts.append(text)

    if matched_texts:
        return "\n\n".join(matched_texts[:10])[:8000]
    return ""