# common/rag_v2.py
import os
import json
import re 
import jieba
import uuid
import datetime
from typing import List
from collections import Counter
import numpy as np  # 【重要】必须添加这个，否则向量计算会报错

try:
    from rank_bm25 import BM25Okapi
    HAS_BM25 = True
except ImportError:
    HAS_BM25 = False
    print("⚠️ 未安装 rank_bm25，请先执行: pip install rank-bm25")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAG_DATA_FILE = os.path.join(BASE_DIR, "rag_data.json")

# 尝试加载向量模型（使用 hf-mirror 成功下载后的本地路径）
_vector_model = None
try:
    from sentence_transformers import SentenceTransformer
    # 【强制离线加载】请将下面的路径替换为您第一步运行后打印的真实路径！
    # 注意：路径中的反斜杠 \ 需要写成双反斜杠 \\，或者直接使用正斜杠 /
    _vector_model = SentenceTransformer("C:/Users/索群/.cache/huggingface/hub/models--thenlper--gte-base-zh/snapshots/71ab7947d6fac5b64aa299e6e40e6c2b2e85976c", local_files_only=True)
    print("✅ 向量模型(GTE)加载成功！")
except Exception as e:
    print(f"⚠️ 向量模型加载失败，将降级为纯 BM25 模式，不影响核心功能: {e}")
    _vector_model = None

def _load_store():
    if os.path.exists(RAG_DATA_FILE):
        with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"files": [], "store": {}}

import jieba
def _tokenize(text: str) -> List[str]:
    # 【核心优化】使用 jieba 进行词级分词，大幅提升 BM25 中文语义匹配的精准度
    return [token for token in jieba.lcut(text) if token.strip()]

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
    """读取文档、智能分块、写入JSON索引。针对CSV自动生成‘统计块’，彻底终结‘最受欢迎’幻觉！"""
    try:
        import pandas as pd
        ext = os.path.splitext(file_path)[1].lower()
        content = ""
        stat_block = ""  # 用于存储统计结果

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
            
            # 【核心增强】如果是数据文件，自动提取品类排行！
            if "coffee_name" in df.columns:
                names = df["coffee_name"].astype(str).tolist()
                counts = Counter(names)
                rank_str = "\n".join([f"{name}: {count} 杯" for name, count in counts.most_common(10)])
                stat_block = f"【系统自动生成的饮品受欢迎程度排行榜】\n{rank_str}"
                
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

        if not content.strip() and not stat_block:
            return "❌ 文件内容为空。"

        chunks = _smart_chunk_text(content)
        file_name = os.path.basename(file_path)
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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

        store.setdefault("files", []).append({
            "file_name": file_name,
            "tags": tags if tags else "(无)",
            "created_at": current_time,
            "chunks": len(chunks) + (1 if stat_block else 0)
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
        
        # 【核心增强】将统计块也写入知识库！
        if stat_block:
            store["store"][target_key].append({
                "id": str(uuid.uuid4()),
                "text": stat_block,
                "tags": tags,
                "file_name": file_name,
                "time": current_time
            })

        with open(RAG_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)

        return f"✅ [V2增强版] 文档已成功索引（共 {len(chunks) + (1 if stat_block else 0)} 个智能分块，含自动统计）。"
    except Exception as e:
        return f"❌ [V2] 文档处理失败: {e}"

def search_knowledge_v2(query: str, tags: str = ""):
    """【V2】混合检索：精准匹配 + BM25 + 直接读取统计块"""
    try:
        REQUIRED_TERMS = ["销售", "收入", "咖啡", "利润", "工资", "统计", "受欢迎", "哪个", "品类", "牛奶", "品种", "饮品", "拿铁", "美式"]
        if not any(term in query for term in REQUIRED_TERMS):
            return ""

        store = _load_store()
        combined_docs = []
        if tags and tags.strip():
            target_key = f"tag_{tags.strip()}"
            combined_docs.extend(store["store"].get(target_key, []))
        if not combined_docs:
            combined_docs.extend(store["store"].get("__global__", []))
            for key in store["store"].keys():
                if key.startswith("tag_"):
                    combined_docs.extend(store["store"][key])

        if not combined_docs:
            return ""

        all_texts = [doc.get("text", "") for doc in combined_docs]

        # 1. 日期精确匹配（保底）
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
            # 2. 【核心修复】只要问到了具体品类、牛奶、饮品，必须直接优先召回物理统计块！
            if any(term in query for term in ["受欢迎", "哪个", "品类", "牛奶", "品种", "饮品", "拿铁", "美式"]):
                for text in all_texts:
                    if "饮品受欢迎程度排行榜" in text:
                        matched_texts.append(text)
            
            # 3. 纯本地 BM25 语义检索兜底
            if not matched_texts and HAS_BM25:
                tokenized_corpus = [_tokenize(text) for text in all_texts]
                bm25 = BM25Okapi(tokenized_corpus)
                scores = bm25.get_scores(_tokenize(query))
                top_indices = scores.argsort()[-10:][::-1]
                for idx in top_indices:
                    if scores[idx] > 0:
                        matched_texts.append(all_texts[idx])

        if matched_texts:
            return "\n\n".join(matched_texts[:10])[:20000]
        return ""
    except Exception as e:
        print(f"###DEBUG### V2混合检索失败: {e}")
        return ""