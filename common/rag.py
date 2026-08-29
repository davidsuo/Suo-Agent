# common/rag.py
import os
import json
import uuid
import datetime
import re
from typing import List

RAG_DATA_FILE = os.path.join(os.getcwd(), "rag_data.json")
GLOBAL_KEY = "__global__"

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

# 【优化1】切分增大，减少切片数量至约75个，提升性能
def _chunk_text(text: str, chunk_size=3000) -> List[str]:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i:i + chunk_size])
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
            # 【优化2】仅保留前300行，防止数据过大，并转为整齐的Markdown表格
            df = df.head(300)
            try:
                content = df.to_markdown(index=False)
            except ImportError:
                content = df.to_string(index=False)
            except Exception:
                content = df.to_string(index=False)
            
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

    # 【优化3】严格限制返回给大模型的文本量，防止卡顿
    if matched_texts:
        # 只截取前几个，总长限制在 2500 字符以内
        result = "\n\n".join(matched_texts[:5])[:2500]
        return result
    return ""