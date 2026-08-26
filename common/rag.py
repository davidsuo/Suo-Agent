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

def _chunk_text(text: str, chunk_size=3000) -> List[str]:
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
    """增强版检索：精确提取月份数据，并直接在Python中完成总和计算"""
    import re
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
    
    # 2. 如果明确指定了年月，直接精确提取该年月所有数据并计算总和
    if target_prefix:
        matched_texts = []
        total_sum = 0.0
        record_count = 0
        
        for doc in docs:
            text = doc.get("text", "")
            # 只要当前文本块中包含了该月份的前缀，就逐行提取
            if target_prefix in text:
                matched_texts.append(text)
                # 提取所有符合的行的价格
                for line in text.splitlines():
                    if target_prefix in line:
                        parts = line.split(',')
                        if len(parts) >= 5:
                            try:
                                price = float(parts[4])
                                total_sum += price
                                record_count += 1
                            except ValueError:
                                pass
        
        if matched_texts:
            # 将精确的计算结果直接返回给模型，模型只需要引用即可！
            result_text = (
                f"\n【月度预计算统计结果】\n"
                f"月份：{target_prefix}\n"
                f"交易笔数：{record_count}\n"
                f"销售总收入：{total_sum:.3f}\n"
                f"（以上结果为Python程序直接从源数据中自动求和的精确结果，无任何估算。）"
            )
            print(f"[RAG] 已精确提取并计算 {target_prefix} 的数据，共 {record_count} 笔，合计 {total_sum:.3f}")
            return "\n\n".join(matched_texts) + result_text
    
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