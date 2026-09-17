# common/rag_v2.py
"""
RAGV2 - ChromaDB(HNSW) + BM25 双路混合检索（企业级，多部门分片）

【架构】
- 向量路：ChromaDB PersistentClient + HNSW 索引，按部门分片（dept_{department}）
- 关键词路：BM25，从 rag_data.json 加载文本，jieba 分词
- 融合：RRF（k=60）+ 阈值过滤
- 返回：结构化对象 {context_text, sources}

【设计原则】
1. 语义优先：向量检索为主，BM25 补充专有名词/错误码精确匹配
2. 数据驱动：列角色识别基于数据特征，不依赖列名硬编码
3. 幂等写入：ChromaDB 用 upsert，避免 delete_collection 引发的索引崩溃
4. 优雅关闭：atexit 清理 ChromaDB 缓存
5. CPU 优化：torch 4 线程 + batch_size=32 + 增量编码

【返回格式】
{
    "context_text": "...",           # 语义上下文，供 LLM 阅读
    "sources": [                     # 来源元数据，供评估/前端使用
        {"doc_id": "IT-01", "chunk_id": "a1b2c3d4",
         "file_name": "kb.md", "department": "it", "score": 0.032}
    ]
}
"""

import os
import json
import re
import time
import uuid
import atexit
import datetime
import threading
from io import StringIO
from typing import List, Dict, Optional, Tuple

import numpy as np
import pandas as pd
import jieba
from rank_bm25 import BM25Okapi

# ==================== 性能优化：i5-1135G7 4 物理核 ====================
try:
    import torch
    torch.set_num_threads(4)
    print(f"✅ torch 线程数设置为 {torch.get_num_threads()}")
except ImportError:
    pass

# ==================== 向量模型加载 ====================
_vector_model = None
try:
    from sentence_transformers import SentenceTransformer
    _vector_model = SentenceTransformer(
        "C:/Users/索群/.cache/huggingface/hub/models--thenlper--gte-base-zh/"
        "snapshots/71ab7947d6fac5b64aa299e6e40e6c2b2e85976c",
        local_files_only=True
    )
    print("✅ 向量模型(GTE)加载成功！")
    # 【性能优化】预热模型，消除首次 encode 的 30 秒冷启动
    import time as _t
    _t0 = _t.time()
    _vector_model.encode(["预热"], normalize_embeddings=True, show_progress_bar=False)
    print(f"✅ 模型预热完成，耗时 {_t.time() - _t0:.2f}s")
except Exception as e:
    print(f"⚠️ 向量模型加载失败，将降级为纯 BM25 模式: {e}")
    _vector_model = None

# ==================== ChromaDB 客户端 ====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 优先使用持久化磁盘路径，将数据库和元数据存储在 Disk
UPLOAD_DIR = os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, 'uploads'))
os.makedirs(UPLOAD_DIR, exist_ok=True)
CHROMA_DIR = os.path.join(UPLOAD_DIR, "chroma_db")
RAG_DATA_FILE = os.path.join(UPLOAD_DIR, "rag_data.json")

_chroma_client = None
try:
    import chromadb
    os.makedirs(CHROMA_DIR, exist_ok=True)
    _chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    print(f"✅ ChromaDB 客户端初始化成功（路径: {CHROMA_DIR}）")
except Exception as e:
    print(f"⚠️ ChromaDB 初始化失败，降级为纯 BM25: {e}")
    _chroma_client = None

# atexit 优雅关闭
def _shutdown_chroma():
    global _chroma_client
    if _chroma_client:
        try:
            _chroma_client.clear_system_cache()
            print("🛑 ChromaDB 已优雅关闭")
        except Exception:
            pass
atexit.register(_shutdown_chroma)

# 【性能与一致性】ChromaDB 写入的串行锁，避免并发 upsert 竞争
_INDEX_LOCK = threading.Lock()

# 部门白名单（用于多 collection 分片）
DEPARTMENTS = [
    "IT", "HR", "Finance", "Sales", "Marketing",
    "Operations", "Legal", "Admin", "Product", "Engineering"
]

# 【US-01 追加】部门中英文别名 → 统一 canonical 名
DEPARTMENT_ALIASES = {
    # IT
    "it": "it", "信息技术": "it", "技术": "it", "技术部": "it",
    # Sales
    "sales": "sales", "销售": "sales", "销售部": "sales", "业务": "sales",
    # HR
    "hr": "hr", "人事": "hr", "人力资源": "hr", "人事部": "hr",
    # Finance
    "finance": "finance", "财务": "finance", "财务部": "finance", "会计": "finance",
    # Marketing
    "marketing": "marketing", "市场": "marketing", "市场部": "marketing",
    # Operations
    "operations": "operations", "运营": "operations", "运营部": "operations",
    # Legal
    "legal": "legal", "法务": "legal", "法务部": "legal",
    # Admin
    "admin": "admin", "行政": "admin", "行政部": "admin", "管理": "admin",
    # Product
    "product": "product", "产品": "product", "产品部": "product",
    # Engineering
    "engineering": "engineering", "研发": "engineering", "研发部": "engineering",
}

# 分块参数（参考《切片原理》：400 token，15% overlap）
CHUNK_CONFIG = {
    "target_chars": 570,       # ≈ 400 token
    "overlap_chars": 85,       # ≈ 60 token
    "min_chunk_chars": 50,
}

# 检索参数
RETRIEVAL_CONFIG = {
    "vector_top_k": 20,       # 从 10 → 20，扩大候选池
    "bm25_top_k": 20,         # 从 10 → 20，扩大候选池
    "rrf_k": 60,
    "rrf_threshold": 0.001,   # 从 0.02 → 0.001，由两路各自阈值把关
    "vector_threshold": 0.30, # 保持，向量相似度门槛
    "final_top_k": 5,
}

# ==================== 分层索引策略 ====================
# 【设计依据】用户对结构化数据的查询通常是精确的（如"2024年7月销售"），
# BM25 的关键词匹配已经足够；而知识库文档的查询往往是模糊的（如"打印机报错"），
# 需要向量语义补充。分层索引在保证质量的同时，避免对 CSV 做昂贵的 GTE 编码。
VECTOR_ENABLED_EXTENSIONS = {".md", ".txt", ".pdf", ".docx"}

# ==================== 部门分片工具 ====================

def _extract_department(tags: str) -> str:
    """
    从 tags 中提取部门，支持中英文别名。
    匹配不到则落到 'general'。
    """
    if not tags:
        return "general"
    tags_lower = tags.lower()
    for alias, canonical in DEPARTMENT_ALIASES.items():
        if alias.lower() in tags_lower:
            return canonical
    return "general"

def _get_collection(department: str):
    """获取或创建部门对应的 collection（幂等）"""
    if not _chroma_client:
        return None
    name = f"dept_{department.lower()}"
    return _chroma_client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )

def _list_all_collections() -> List[str]:
    try:
        collections = _chroma_client.list_collections()
        names = []
        for c in collections:
            name = c if isinstance(c, str) else c.name
            if name.startswith("dept_"):
                names.append(name)
        return names
    except Exception as e:
        print(f"⚠️ 列出 collection 失败: {e}")
        return []


# ==================== 数据加载 ====================

def _load_store() -> dict:
    if os.path.exists(RAG_DATA_FILE):
        with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"files": [], "store": {}}


def _tokenize(text: str) -> List[str]:
    return [t for t in jieba.lcut(text) if t.strip()]


# ==================== 数据驱动的日期列识别（仅用于导航标注） ====================

def _find_date_column(df: pd.DataFrame) -> Optional[str]:
    """
    数据驱动地找日期列：尝试用 pd.to_datetime 解析每一列，
    返回第一个"能解析出多个不同日期"的列。
    
    【设计原则】
    - 只看数据本身，不看列名
    - 排除纯时间列（如 6:14，年月日都相同）
    - 找不到就返回 None，不硬性要求
    """
    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        sample = series.head(min(50, len(series)))
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            valid = parsed.dropna()
            if len(valid) / len(sample) > 0.8 and valid.dt.date.nunique() > 1:
                return col
        except Exception:
            continue
    return None


# ==================== 通用递归切分 ====================

def _universal_chunk(text: str, separators: List[str],
                     target_chars: int, overlap_chars: int) -> List[str]:
    """递归切分：按分隔符优先级逐级切，直到满足大小"""
    return _recursive_split(text, separators, target_chars, overlap_chars, 0)


def _recursive_split(text: str, separators: List[str],
                     target_chars: int, overlap_chars: int,
                     depth: int) -> List[str]:
    if depth >= len(separators) - 1:
        return _hard_split(text, target_chars, overlap_chars)

    sep = separators[depth]
    if not sep:
        return _hard_split(text, target_chars, overlap_chars)

    parts = text.split(sep)
    if len(parts) == 1:
        return _recursive_split(text, separators, target_chars, overlap_chars, depth + 1)

    parts = [p + sep if i < len(parts) - 1 else p for i, p in enumerate(parts)]

    chunks, buffer = [], ""
    for part in parts:
        if len(buffer) + len(part) <= target_chars:
            buffer += part
        else:
            if buffer:
                chunks.append(buffer)
            if len(part) > target_chars:
                sub = _recursive_split(part, separators, target_chars, overlap_chars, depth + 1)
                chunks.extend(sub[:-1])
                buffer = sub[-1] if sub else ""
            else:
                buffer = part
    if buffer:
        chunks.append(buffer)
    return _apply_overlap(chunks, overlap_chars)


def _hard_split(text: str, target_chars: int, overlap_chars: int) -> List[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + target_chars
        chunks.append(text[start:end])
        start = end - overlap_chars
        if start >= len(text):
            break
    return chunks


def _apply_overlap(chunks: List[str], overlap_chars: int) -> List[str]:
    if len(chunks) <= 1:
        return chunks
    overlapped = []
    for i, chunk in enumerate(chunks):
        if i < len(chunks) - 1:
            overlapped.append(chunk + chunks[i + 1][:overlap_chars])
        else:
            overlapped.append(chunk)
    return overlapped


# ==================== Markdown 严格切分 ====================

def _chunk_markdown(text: str) -> List[str]:
    """按 '## ' 二级标题切，每个块是一个独立知识条目"""
    blocks = re.split(r'\n(?=##\s)', text)
    return [b.strip() for b in blocks if b.strip()]


# ==================== 表格聚合增强器（数据驱动） ====================

# ==================== 数据驱动的日期列识别（仅用于导航标注） ====================

def _find_date_column(df: pd.DataFrame) -> Optional[str]:
    """
    数据驱动地找日期列：尝试用 pd.to_datetime 解析每一列，
    返回第一个"能解析出多个不同日期"的列。
    
    【设计原则】
    - 只看数据本身，不看列名
    - 排除纯时间列（如 6:14，年月日都相同）
    - 找不到就返回 None，不硬性要求
    """
    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        sample = series.head(min(50, len(series)))
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            valid = parsed.dropna()
            if len(valid) / len(sample) > 0.8 and valid.dt.date.nunique() > 1:
                return col
        except Exception:
            continue
    return None


# ==================== 表格分块（真 RAG 原理） ====================

def _chunk_table(text: str) -> List[str]:
    """
    表格分块（按月切，符合 RAG 原理）：
    - 检测到日期列 → 按月份切，每月 1 块
    - 每块 = 数据范围导航标注 + 表头 + 该月全部原始记录
    - 不做任何聚合（让大模型自己算）
    - 无日期列 → 按固定行数切

    【导航标注设计】
    同时包含 3 种日期表达，让用户任何问法都能 BM25 命中：
    - 中文："2024年10月份"
    - 数字："2024/10/1 ~ 2024/10/31"
    - 计数："共 426 条"
    """
    chunks = []
    try:
        df = pd.read_csv(StringIO(text))
    except Exception as e:
        print(f"⚠️ CSV 解析失败，降级为按字符切: {e}")
        return _hard_split(text, CHUNK_CONFIG["target_chars"] * 3, 100)

    if len(df) == 0:
        return chunks

    # 数据驱动找日期列
    date_col = _find_date_column(df)

    # ========== 无日期列：回退到固定行数切 ==========
    if not date_col:
        print(f"【诊断-chunk】未检测到日期列，按固定行数（50行/块）切分")
        header = ",".join(df.columns)
        # 【US-01 方案A】无日期列时同样生成摘要块
        summary_lines = [
            "【数据摘要】表格数据文件",
            f"总行数：{len(df)}",
            f"列名：{','.join(str(c) for c in df.columns)}",
        ]
        chunks.append("\n".join(summary_lines))
        rows_per_chunk = 50
        overlap_rows = 5
        i = 0
        while i < len(df):
            end = min(i + rows_per_chunk, len(df))
            block = df.iloc[i:end]
            chunk = f"【数据范围：第 {i+1} ~ {end} 行，共 {len(block)} 条】\n"
            chunk += header + "\n"
            chunk += block.to_csv(index=False, header=False)
            chunks.append(chunk)
            if end >= len(df):
                break
            i += rows_per_chunk - overlap_rows
        print(f"【诊断-chunk】表格分块完成：{len(chunks)} 块（按行）")
        return chunks

    # 【US-01 方案A】生成"数据摘要块"作为第一块
    # 目的：让 CSV 也有 1 个向量进 ChromaDB，使 dept_sales collection 存在
    # 用途：US-01 领域筛选的"入口信号"，不参与实际检索
    summary_lines = [
        f"【数据摘要】表格数据文件",
        f"总行数：{len(df)}",
        f"列名：{','.join(str(c) for c in df.columns)}",
        "样例数据（前3行）：",
    ]
    try:
        summary_lines.append(df.head(3).to_csv(index=False, header=False).strip())
    except Exception:
        pass
    summary_chunk = "\n".join(summary_lines)

    # 把摘要块插到 chunks 列表最前面（后续按月切的块会 append 到后面）
    # 注意：此处需要改函数返回值结构，见下方说明

    # ========== 有日期列：按月份切 ==========
    print(f"【诊断-chunk】检测到日期列: '{date_col}'，按月切分")
    df["_parsed_date_"] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
    df["_period_"] = df["_parsed_date_"].dt.to_period("M")

    # 【US-01 方案A】摘要块作为第一块
    # 【关键】加入高频分类值作为"领域指纹"，让无关查询相似度显著下降
    summary_lines = [
        "【数据摘要】表格数据文件",
        f"总行数：{len(df)}",
        f"列名：{','.join(str(c) for c in df.columns if not str(c).startswith('_'))}",
    ]
    # 提取前 3 个低基数列的 Top 5 高频值，作为领域特征
    try:
        obj_cols = [c for c in df.columns if df[c].dtype == "object" and not str(c).startswith("_")]
        for col in obj_cols[:3]:
            top_vals = df[col].value_counts().head(5).index.tolist()
            top_vals = [str(v) for v in top_vals if str(v).strip()]
            if top_vals:
                summary_lines.append(f"{col} 高频取值：{', '.join(top_vals)}")
    except Exception:
        pass
    summary_lines.append("样例数据（前3行）：")
    try:
        summary_lines.append(df.head(3).iloc[:, :6].to_csv(index=False, header=False).strip())
    except Exception:
        pass
    chunks.append("\n".join(summary_lines))
    print(f"【诊断-chunk】已生成数据摘要块（含领域特征）")

    # 记录原始列（排除辅助列）
    other_cols = [c for c in df.columns if c not in ["_parsed_date_", "_period_"]]
    header = ",".join(other_cols)

    for period, group in df.groupby("_period_", sort=True):
        if pd.isna(period):
            continue

        # 构造 3 种日期表达
        year = period.year
        month = period.month
        start_date = group["_parsed_date_"].min().strftime("%Y/%m/%d")
        end_date = group["_parsed_date_"].max().strftime("%Y/%m/%d")

        # 导航标注（含中文、数字、计数三种表达）
        nav = f"【数据范围：{year}年{month}月份（{start_date} ~ {end_date}），共 {len(group)} 条】\n"

        # 组装块（导航 + 表头 + 原始记录）
        group_clean = group[other_cols]
        chunk = nav + header + "\n" + group_clean.to_csv(index=False, header=False)
        chunks.append(chunk)

        print(f"【诊断-chunk】  {year}年{month}月: {len(group)} 行，块大小 {len(chunk)} 字符")

    print(f"【诊断-chunk】表格分块完成：{len(chunks)} 块（按月）")
    return chunks


# ==================== 分块主函数 ====================

def _smart_chunk_text(text: str, file_ext: str) -> List[str]:
    """统一分块入口，按文件类型分发"""
    ext = file_ext.lower()

    if ext == ".md":
        chunks = _chunk_markdown(text)
        print(f"【诊断-chunk】Markdown 按 ## 切：{len(chunks)} 块")
    elif ext in [".csv", ".xlsx", ".xls"]:
        chunks = _chunk_table(text)
        print(f"【诊断-chunk】表格分块：{len(chunks)} 块")
    elif ext in [".pdf", ".docx"]:
        separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
        chunks = _universal_chunk(text, separators,
                                   CHUNK_CONFIG["target_chars"],
                                   CHUNK_CONFIG["overlap_chars"])
        print(f"【诊断-chunk】{ext} 段落递归切分：{len(chunks)} 块")
    else:
        separators = ["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""]
        chunks = _universal_chunk(text, separators,
                                   CHUNK_CONFIG["target_chars"],
                                   CHUNK_CONFIG["overlap_chars"])
        print(f"【诊断-chunk】通用递归切分：{len(chunks)} 块")

    # 过滤过小的碎片（但保留聚合块，它们天然短小）
    min_size = CHUNK_CONFIG["min_chunk_chars"]
    result = []
    for c in chunks:
        s = c.strip()
        if not s:
            continue
        # 聚合块（以【开头）无论多短都保留
        is_aggregate = s.startswith("【")
        if is_aggregate or len(s) >= min_size:
            result.append(s)
    return result


# ==================== 文档索引 ====================

def index_document_v2(file_path: str, tags: str = "") -> str:
    """
    处理上传文档：解析 → 分块 → 写 rag_data.json → 分层写 ChromaDB → 重建 BM25

    【支持格式】.md / .txt / .csv / .xlsx / .xls / .pdf / .docx
    【分层策略】
    - 语义类（.md/.txt/.pdf/.docx）：全块编码进 ChromaDB
    - 表格类（.csv/.xlsx/.xls）：只编码摘要块，其余仅 BM25
    """
    # 【关键修复】统一在函数顶部 import pandas，避免局部导入导致 UnboundLocalError
    import pandas as pd

    with _INDEX_LOCK:
        try:
            ext = os.path.splitext(file_path)[1].lower()
            content = ""

            # ========== 1. 解析（支持 7 种格式） ==========
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
                except ImportError:
                    return "❌ 缺少 pypdf 库，请执行: pip install pypdf"
                try:
                    reader = PdfReader(file_path)
                    for page in reader.pages:
                        page_text = page.extract_text() or ""
                        content += page_text + "\n"
                except Exception as e:
                    return f"❌ PDF 解析失败: {e}"
                if not content.strip():
                    return "❌ PDF 内容为空或无法提取文本（可能是扫描版 PDF，需 OCR）"

            elif ext == ".docx":
                try:
                    from docx import Document
                except ImportError:
                    return "❌ 缺少 python-docx 库，请执行: pip install python-docx"
                try:
                    doc = Document(file_path)
                    # 提取段落
                    for para in doc.paragraphs:
                        if para.text.strip():
                            content += para.text + "\n"
                    # 提取表格
                    for table in doc.tables:
                        for row in table.rows:
                            row_text = " | ".join(cell.text for cell in row.cells)
                            content += row_text + "\n"
                except Exception as e:
                    return f"❌ DOCX 解析失败: {e}"
                if not content.strip():
                    return "❌ DOCX 内容为空"

            else:
                return f"❌ 不支持的文件格式: {ext}（支持 .md/.txt/.csv/.xlsx/.pdf/.docx）"

            if not content.strip():
                return "❌ 文件内容为空。"

            # ========== 2. 分块 ==========
            chunks = _smart_chunk_text(content, file_ext=ext)
            file_name = os.path.basename(file_path)
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"【诊断-index】文件 '{file_name}' 分块数: {len(chunks)}")

            # ========== 3. 部门分片 ==========
            department = _extract_department(tags)
            print(f"【诊断-index】文件 '{file_name}' → 部门分片: {department}")

            # ========== 4. 写入 rag_data.json ==========
            store = _load_store()
            store["files"] = [f for f in store.get("files", []) if f.get("file_name") != file_name]
            for key in list(store.get("store", {}).keys()):
                store["store"][key] = [d for d in store["store"][key] if d.get("file_name") != file_name]

            target_key = f"dept_{department}"
            store.setdefault("store", {}).setdefault(target_key, [])
            new_docs = []
            for chunk in chunks:
                doc_id = str(uuid.uuid4())
                doc = {
                    "id": doc_id,
                    "text": chunk,
                    "tags": tags,
                    "department": department,
                    "file_name": file_name,
                    "time": current_time,
                }
                store["store"][target_key].append(doc)
                new_docs.append(doc)

            print(f"【诊断-index】准备写入的块数: {len(new_docs)}")

            # ========== 5. 提取 schema（供 LLM 语义理解） ==========
            file_schema = None
            try:
                if ext in [".csv", ".xlsx", ".xls"]:
                    if ext == ".csv":
                        _df = pd.read_csv(file_path)
                    else:
                        _df = pd.read_excel(file_path)
                    _df = _df.fillna("")
                    from common.tools import extract_schema
                    file_schema = extract_schema(_df)
                    print(f"【诊断-schema】{file_name} schema 提取完成：{len(file_schema['columns'])} 列")
            except Exception as e:
                print(f"⚠️ schema 提取失败: {e}")

            # 写入文件元数据
            file_entry = {
                "file_name": file_name,
                "tags": tags if tags else "(无)",
                "department": department,
                "created_at": current_time,
                "chunks": len(chunks),
            }
            if file_schema:
                file_entry["schema"] = file_schema
            store.setdefault("files", []).append(file_entry)

            with open(RAG_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)

            # ========== 6. 分层写入 ChromaDB ==========
            use_vector = ext in VECTOR_ENABLED_EXTENSIONS
            is_csv_like = ext in [".csv", ".xlsx", ".xls"]

            if use_vector:
                vector_docs = new_docs
                print(f"【诊断-index】{ext} 语义类 → 编码全部 {len(new_docs)} 块")
            elif is_csv_like and new_docs:
                vector_docs = new_docs[:1]   # 只取第一块（摘要块）
                print(f"【诊断-index】{ext} 表格类 → 只编码摘要块 1 条（其余 {len(new_docs)-1} 块仅 BM25）")
            else:
                vector_docs = []

            if not vector_docs:
                print(f"【诊断-index】{ext} 跳过向量索引")
            elif _chroma_client and _vector_model:
                t0 = time.time()
                collection = _get_collection(department)
                if collection:
                    try:
                        collection.delete(where={"file_name": file_name})
                    except Exception as e:
                        print(f"⚠️ ChromaDB 删除旧块: {e}")

                    texts = [d["text"] for d in vector_docs]
                    ids = [d["id"] for d in vector_docs]
                    metadatas = [
                        {"file_name": file_name, "department": department,
                         "tags": tags or "", "time": current_time}
                        for _ in vector_docs
                    ]

                    print(f"【诊断-index】开始 GTE 编码 {len(texts)} 条...")
                    t_enc = time.time()
                    embeddings = _vector_model.encode(
                        texts, normalize_embeddings=True,
                        batch_size=32, show_progress_bar=False
                    ).tolist()
                    print(f"【诊断-index】GTE 编码完成，耗时 {time.time() - t_enc:.2f}s")

                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        documents=texts,
                        metadatas=metadatas,
                    )
                    print(f"✅ ChromaDB 写入 {len(ids)} 条，总耗时 {time.time() - t0:.2f}s")
            else:
                print(f"⚠️ ChromaDB 或向量模型不可用，跳过向量索引")

            # ========== 7. 重建 BM25 ==========
            t0 = time.time()
            _build_bm25()
            print(f"✅ BM25 重建完成，耗时 {time.time() - t0:.2f}s")

            return f"✅ 文档索引成功（{len(chunks)} 块，部门: {department}）"

        except Exception as e:
            import traceback
            print(f"❌ 索引异常: {traceback.format_exc()}")
            return f"❌ 文档处理失败: {e}"

# ==================== BM25 索引 ====================

_bm25_index: Optional[BM25Okapi] = None
_bm25_docs: List[dict] = []


def _build_bm25():
    """
    从 rag_data.json 构建 BM25 索引。

    【关键设计】
    BM25 索引的文档 = 导航标注 + 前 500 字符。
    原因：
    - 完整块可能 25000+ 字符，BM25 的长度归一化（b=0.75）会惩罚长文档，
      导致包含稀有词（如"10月"）的长块分数被压平。
    - 只取头部 500 字符，长度接近，稀有词的权重能真正体现。
    - 表头 + 少量样本足够捕捉查询关键词，精确内容由大模型读取完整块。
    """
    global _bm25_index, _bm25_docs
    store = _load_store()
    all_docs = []
    for key, docs in store.get("store", {}).items():
        all_docs.extend(docs)

    _bm25_docs = all_docs
    if all_docs:
        # BM25 索引文本 = 导航标注（第一行） + 前 500 字符
        bm25_texts = []
        for d in all_docs:
            text = d.get("text", "")
            first_line, _, rest = text.partition("\n")
            bm25_text = first_line + "\n" + rest[:500]
            bm25_texts.append(bm25_text)
        tokenized = [_tokenize(t) for t in bm25_texts]
        _bm25_index = BM25Okapi(tokenized)
        print(f"【诊断-bm25】索引 {len(all_docs)} 块（每块截断至 ~500 字符）")
    else:
        _bm25_index = None


# 模块启动时构建一次
_build_bm25()


# ==================== 混合检索 ====================

def search_knowledge_v2(query: str, extra_params: str = "") -> dict:
    """
    双路混合检索：
      1. 向量路：ChromaDB 多 collection 查询 → Top 10
      2. 关键词路：BM25 全量打分 → Top 10
      3. RRF 融合（k=60）
      4. 阈值过滤（< 0.02 视为不相关）
      5. 取 Top 3

    返回结构化对象 {context_text, sources}
    """
    global _vector_model, _chroma_client, _bm25_index, _bm25_docs

    vector_results: Dict[str, int] = {}   # chunk_id -> rank
    vector_meta: Dict[str, dict] = {}     # chunk_id -> metadata

    # ========== 路 1：向量检索（ChromaDB 多 collection） ==========
    if _chroma_client and _vector_model:
        try:
            t0 = time.time()
            query_emb = _vector_model.encode([query], normalize_embeddings=True, show_progress_bar=False).tolist()
            collection_names = _list_all_collections()

            # 【US-01】每个 collection 独立检索，计算 max_similarity
            collection_stats = {}   # cname -> {"max_sim": float, "hits": [...]}
            for cname in collection_names:
                try:
                    col = _chroma_client.get_collection(name=cname)
                    res = col.query(
                        query_embeddings=query_emb,
                        n_results=RETRIEVAL_CONFIG["vector_top_k"],
                        include=["documents", "metadatas", "distances"],
                    )
                    hits = []
                    max_sim = 0.0
                    if res and res["ids"] and res["ids"][0]:
                        for i, doc_id in enumerate(res["ids"][0]):
                            sim = 1.0 - res["distances"][0][i]
                            if sim > max_sim:
                                max_sim = sim
                            if sim >= RETRIEVAL_CONFIG["vector_threshold"]:
                                hits.append((sim, doc_id, res["metadatas"][0][i], res["documents"][0][i]))
                    collection_stats[cname] = {"max_sim": max_sim, "hits": hits}
                except Exception as e:
                    print(f"⚠️ 查询 collection {cname} 失败: {e}")

            # 【可观测】领域筛选绝对阈值
            #   0.35 → 0.55：过滤"擦边"误召回
            #   日志格式：###RAG检索### 阈值判定 | best_sim=0.xxxx | 阈值=0.55 | 判定=...
            ABSOLUTE_THRESHOLD = 0.55
            if collection_stats:
                best_sim = max(s["max_sim"] for s in collection_stats.values())
                summary = {c: round(s["max_sim"], 3) for c, s in collection_stats.items()}

                if best_sim < ABSOLUTE_THRESHOLD:
                    print(
                        f"###RAG检索### 阈值判定 | best_sim={best_sim:.4f} | "
                        f"阈值={ABSOLUTE_THRESHOLD} | 判定=拒绝 | all={summary}"
                    )
                    all_hits = []
                else:
                    selected = [c for c, s in collection_stats.items()
                                if best_sim - s["max_sim"] < 0.05]
                    print(
                        f"###RAG检索### 阈值判定 | best_sim={best_sim:.4f} | "
                        f"阈值={ABSOLUTE_THRESHOLD} | 判定=通过 | "
                        f"selected={selected} | all={summary}"
                    )
                    all_hits = []
                    for cname in selected:
                        all_hits.extend(collection_stats[cname]["hits"])
                    all_hits = []
                    for cname in selected:
                        all_hits.extend(collection_stats[cname]["hits"])
            else:
                all_hits = []

            # 【US-01 追加】无相关领域时，跳过 BM25，直接返回空
            # 防止 BM25 全局检索污染无关问题的上下文
            if not all_hits:
                print(f"【US-01】无相关领域 → 跳过 BM25，整体返回空")
                return {"context_text": "", "sources": []}

            all_hits.sort(key=lambda x: x[0], reverse=True)
            for rank, (sim, doc_id, meta, doc_text) in enumerate(all_hits[:RETRIEVAL_CONFIG["vector_top_k"]]):
                vector_results[doc_id] = rank
                vector_meta[doc_id] = {"meta": meta, "text": doc_text, "similarity": sim}
            print(f"【诊断-rag_v2】向量路命中 {len(vector_results)} 条，耗时 {time.time() - t0:.3f}s")
        except Exception as e:
            print(f"⚠️ 向量检索失败: {e}")

    # ========== 路 2：BM25 关键词检索 ==========
    bm25_results: Dict[str, int] = {}
    bm25_meta: Dict[str, dict] = {}
    if _bm25_index and _bm25_docs:
        try:
            t0 = time.time()
            scores = _bm25_index.get_scores(_tokenize(query))
            top_indices = np.argsort(scores)[::-1][:RETRIEVAL_CONFIG["bm25_top_k"]]
            for rank, idx in enumerate(top_indices):
                if scores[idx] > 0:
                    doc = _bm25_docs[idx]
                    bm25_results[doc["id"]] = rank
                    bm25_meta[doc["id"]] = {"meta": doc, "score": float(scores[idx])}
            print(f"【诊断-rag_v2】BM25 路命中 {len(bm25_results)} 条，耗时 {time.time() - t0:.3f}s")
        except Exception as e:
            print(f"⚠️ BM25 检索失败: {e}")

    # ========== RRF 融合 ==========
    rrf_scores: Dict[str, float] = {}
    k = RETRIEVAL_CONFIG["rrf_k"]
    for doc_id, rank in vector_results.items():
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
    for doc_id, rank in bm25_results.items():
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)

    if not rrf_scores:
        print("【诊断-rag_v2】两路均无命中，返回空（负样本正确处理）")
        return {"context_text": "", "sources": []}

    sorted_docs = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    
    # 【诊断】打印 RRF 融合后 Top 5，便于追踪命中
    print(f"【诊断-rrf】融合后 Top 5:")
    for doc_id, score in sorted_docs[:5]:
        print(f"    score={score:.4f} | doc_id={doc_id[:8]}")
    
    threshold = RETRIEVAL_CONFIG["rrf_threshold"]
    filtered = [(doc_id, score) for doc_id, score in sorted_docs if score > threshold]

    if not filtered:
        print("【诊断-rag_v2】RRF 分数低于阈值，返回空（负样本正确处理）")
        return {"context_text": "", "sources": []}

    # ========== 组装 Top 3 ==========
    top_k = filtered[:RETRIEVAL_CONFIG["final_top_k"]]
    doc_map = {d["id"]: d for d in _bm25_docs}
    context_parts = []
    sources = []

    for doc_id, score in top_k:
        # 优先用 BM25 路的原始文本；否则用向量路的
        doc = doc_map.get(doc_id)
        text = doc["text"] if doc else vector_meta.get(doc_id, {}).get("text", "")
        if not text:
            continue

        context_parts.append(text)

        # 从文本提取语义 ID（如 [IT-01]）
        id_match = re.search(r'\[([A-Z]+-\d+)\]', text)
        semantic_id = id_match.group(1) if id_match else None

        meta = doc if doc else vector_meta.get(doc_id, {}).get("meta", {})
        sources.append({
            "doc_id": semantic_id,
            "chunk_id": doc_id[:8],
            "file_name": meta.get("file_name", ""),
            "department": meta.get("department", ""),
            "score": round(score, 4),
        })

    print(f"【诊断-rag_v2】最终返回 {len(sources)} 条，RRF 分数: {[s['score'] for s in sources]}")
    return {
        "context_text": "\n\n".join(context_parts),
        "sources": sources,
    }