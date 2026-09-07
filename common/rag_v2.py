# common/rag_v2.py
import os
import json
import re
import uuid
import datetime
from typing import List
try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("⚠️ 未安装 rank_bm25，请先执行: pip install rank-bm25")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAG_DATA_FILE = os.path.join(BASE_DIR, "rag_data.json")

def _load_store():
    if os.path.exists(RAG_DATA_FILE):
        with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"files": [], "store": {}}

def _tokenize(text: str) -> List[str]:
    # 简单分词，兼容中文和英文
    return re.findall(r'[\u4e00-\u9fa5]|[a-zA-Z0-9]+', text)

def _smart_chunk_text(text: str, max_chunk_size: int = 1500) -> List[str]:
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
    """【V2完整版】读取文档、智能分块、写入JSON索引（避免HNSW崩溃）"""
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
                return "❌ 缺少 pypdf 库，请 pip install pypdf"
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
                return "❌ 缺少 python-docx 库，请 pip install python-docx"
        else:
            return f"❌ 不支持的文件格式: {ext}"

        if not content.strip():
            return "❌ 文件内容为空。"

        chunks = _smart_chunk_text(content)
        file_name = os.path.basename(file_path)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 读取并清理旧数据
        store = {"files": [], "store": {}}
        if os.path.exists(RAG_DATA_FILE):
            try:
                with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                store = {"files": [], "store": {}}

        store["files"] = [f for f in store.get("files", []) if f.get("file_name") != file_name]
        for key in list(store.get("store", {}).keys()):
            store["store"][key] = [doc for doc in store["store"][key] if doc.get("file_name") != file_name]

        # 写入新数据
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

        return f"✅ [V2实验版] 文档已成功索引（共 {len(chunks)} 个智能分块）。"
    except Exception as e:
        return f"❌ [V2] 文档处理失败: {e}"

def search_knowledge_v2(query: str, tags: str = ""):
    """【V2实验版】混合检索：关键词精确匹配 + BM25 语义召回"""
    try:
        store = _load_store()
        combined_docs = []
        if tags and tags.strip():
            target_key = f"tag_{tags.strip()}"
            combined_docs.extend(store["store"].get(target_key, []))
        # 如果没传标签，遍历所有标签库兜底
        if not combined_docs:
            combined_docs.extend(store["store"].get("__global__", []))
            for key in store["store"].keys():
                if key.startswith("tag_"):
                    combined_docs.extend(store["store"][key])

        if not combined_docs:
            return ""

        all_texts = [doc.get("text", "") for doc in combined_docs]

        # 1. 日期/数值精确匹配（保底）
        _year_match = re.search(r'(20\d{2})', query)
        _month_match = re.search(r'(\d{1,2})月份', query)
        target_prefix = ""
        if _year_match and _month_match:
            target_prefix = f"{_year_match.group(1)}/{int(_month_match.group(1))}/"
        
        matched_texts = []
        if target_prefix:
            for text in all_texts:
                if target_prefix in text:
                    matched_texts.append(text)
        else:
            # 2. BM25 稀疏语义检索
            if HAS_BM25:
                tokenized_corpus = [_tokenize(text) for text in all_texts]
                if tokenized_corpus:
                    bm25 = BM25Okapi(tokenized_corpus)
                    scores = bm25.get_scores(_tokenize(query))
                    top_indices = scores.argsort()[-10:][::-1]
                    for idx in top_indices:
                        if scores[idx] > 0:
                            matched_texts.append(all_texts[idx])
            else:
                # 降级：简单的关键词包含匹配
                for text in all_texts:
                    if any(word in text for word in _tokenize(query)):
                        matched_texts.append(text)

        if matched_texts:
            return "\n\n".join(matched_texts[:10])[:20000]
        return ""
    except Exception as e:
        print(f"###DEBUG### V2混合检索失败: {e}")
        return ""