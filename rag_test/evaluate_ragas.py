# rag_test/evaluate_ragas.py
"""
RAGAS 独立评估脚本（US-12 阶段一）—— 完整版

沿用 ascore 直调方案（已验证可跑通），对 41 条真实样本批量评估。
"""
import os
import sys
import json
import asyncio
import argparse
import warnings
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
warnings.filterwarnings("ignore")

from openai import AsyncOpenAI
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    Faithfulness,
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
)
from ragas.embeddings import HuggingFaceEmbeddings

# 复用现有 RealRAGClient
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evaluate_rag_live import RealRAGClient

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAG_DATA_FILE = os.path.join(
    os.getenv("UPLOAD_DIR", os.path.join(BASE_DIR, 'uploads')),
    "rag_data.json"
)

# 并发控制（避免 API rate limit）
MAX_CONCURRENCY = 5


# ==================== 加载 ID → 上下文文本 ====================
import re as _re

def _load_id_to_text_index() -> dict:
    if not os.path.exists(RAG_DATA_FILE):
        print(f"⚠️ rag_data.json 不存在：{RAG_DATA_FILE}")
        return {}
    with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
        store = json.load(f)
    id_to_chunks = {}
    for _, docs in store.get("store", {}).items():
        for doc in docs:
            text = doc.get("text", "")
            m = _re.search(r'\[([A-Z]+-\d+)\]', text)
            if m:
                id_to_chunks.setdefault(m.group(1), []).append(text)
    return {sid: "\n".join(chunks) for sid, chunks in id_to_chunks.items()}


def ids_to_context_texts(ids: list, id_index: dict) -> list:
    return [id_index[sid] for sid in ids if sid in id_index]


# ==================== 测试集加载 + 采集 ====================
async def load_test_set(dataset_path: str) -> list:
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [s for s in data if s.get("type") in ("exact", "semantic", "hybrid")]


async def collect_rag_responses(dataset: list, base_url: str) -> list:
    # 【US-12 修复】会话加时间戳前缀，彻底隔离每次评估的历史
    # 避免 memory.json 里旧 session 被复用，导致 LLM 产生"你已问过N次"的元对话
    import time as _time
    ts = int(_time.time())
    # 【关键】必须用已存在的用户作为前缀（如 alice），否则后端会因"用户不存在"拒绝
    # 后缀带上时间戳，保证每次评估的 session 都唯一，不与历史混淆
    prefix = f"alice_ragas_{ts}"

    client = RealRAGClient(base_url=base_url)
    # 覆盖 RealRAGClient 的 session_id 生成逻辑
    original_generate = client.generate

    async def generate_with_isolated_session(query: str):
        client._counter += 1
        session_id = f"{prefix}_{client._counter:03d}"
        # 直接复用 RealRAGClient 的内部逻辑，只改 session_id
        import httpx, json as _json
        payload = {"session_id": session_id, "query": query, "user_text": query}
        answer_content = ""
        context_list = []
        async with httpx.AsyncClient(timeout=180) as http_client:
            try:
                async with http_client.stream("POST", f"{client.base_url}/api/chat", json=payload) as response:
                    if response.status_code != 200:
                        return {"answer": f"请求失败: {response.status_code}", "contexts": []}
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                continue
                            try:
                                data = _json.loads(data_str)
                                if data.get("type") == "answer":
                                    answer_content += data.get("content", "")
                                    new_contexts = data.get("contexts") or []
                                    for cid in new_contexts:
                                        if cid not in context_list:
                                            context_list.append(cid)
                            except Exception:
                                pass
                print(f"【诊断-评估器】问题: {query[:30]}...")
                print(f"【诊断-评估器】session={session_id} contexts: {context_list}")
                return {"answer": answer_content, "contexts": context_list}
            except Exception as e:
                print(f"【诊断-评估器】连接失败: {type(e).__name__}: {e}")
                return {"answer": f"网络错误: {e}", "contexts": []}

    # 用新的 generate 替换
    client.generate = generate_with_isolated_session
    samples = []
    for i, item in enumerate(dataset, 1):
        question = item["question"]
        print(f"[{i}/{len(dataset)}] 采集：{question[:40]}...")
        result = await client.generate(question)
        samples.append({
            "question": question,
            "answer": result.get("answer", ""),
            "contexts_ids": result.get("contexts", []),
            "ground_truth_ids": item.get("ground_truth", []),
        })
    return samples
    samples = []
    for i, item in enumerate(dataset, 1):
        question = item["question"]
        print(f"[{i}/{len(dataset)}] 采集：{question[:40]}...")
        result = await client.generate(question)
        samples.append({
            "question": question,
            "answer": result.get("answer", ""),
            "contexts_ids": result.get("contexts", []),
            "ground_truth_ids": item.get("ground_truth", []),
        })
    return samples


# ==================== 逐条评估 ====================
async def evaluate_one(sample: dict, id_index: dict, metrics: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        question = sample["question"]
        answer = sample["answer"]
        contexts = ids_to_context_texts(sample["contexts_ids"], id_index)
        reference = "\n".join(ids_to_context_texts(sample["ground_truth_ids"], id_index))

        result = {"question": question, "answer": answer}

        if not contexts:
            print(f"⚠️ [{question[:30]}] 无上下文，跳过 4 个指标")
            result.update({
                "faithfulness": None, "answer_relevancy": None,
                "context_precision": None, "context_recall": None,
            })
            return result

        if not reference:
            print(f"⚠️ [{question[:30]}] 无 reference，跳过 context_precision/recall")
            result.update({"context_precision": None, "context_recall": None})
        try:
            r = await metrics["faithfulness"].ascore(
                user_input=question, response=answer, retrieved_contexts=contexts,
            )
            result["faithfulness"] = r.value
        except Exception as e:
            print(f"❌ [{question[:30]}] Faithfulness 失败: {e}")
            result["faithfulness"] = None

        try:
            r = await metrics["answer_relevancy"].ascore(
                user_input=question, response=answer,
            )
            result["answer_relevancy"] = r.value
        except Exception as e:
            print(f"❌ [{question[:30]}] AnswerRelevancy 失败: {e}")
            result["answer_relevancy"] = None

        if reference:
            try:
                r = await metrics["context_precision"].ascore(
                    user_input=question, retrieved_contexts=contexts, reference=reference,
                )
                result["context_precision"] = r.value
            except Exception as e:
                print(f"❌ [{question[:30]}] ContextPrecision 失败: {e}")
                result["context_precision"] = None

            try:
                r = await metrics["context_recall"].ascore(
                    user_input=question, retrieved_contexts=contexts, reference=reference,
                )
                result["context_recall"] = r.value
            except Exception as e:
                print(f"❌ [{question[:30]}] ContextRecall 失败: {e}")
                result["context_recall"] = None

        print(f"✅ [{question[:30]}] F={result.get('faithfulness')}, AR={result.get('answer_relevancy')}, "
              f"CP={result.get('context_precision')}, CR={result.get('context_recall')}")
        return result


# ==================== 主流程 ====================
async def main():
    parser = argparse.ArgumentParser(description="RAGAS 独立评估（完整版）")
    parser.add_argument("dataset", nargs="?", default="rag_test/test_set.json")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()

    target_url = "http://127.0.0.1:10000" if args.local else "https://suo-agent.onrender.com"
    print(f"⚠️ RAGAS 评估目标: {target_url}\n")

    # 1. 加载测试集
    dataset = await load_test_set(args.dataset)
    print(f"加载 {len(dataset)} 条 positive 样本\n")

    # 2. 采集 RAG 回答
    samples = await collect_rag_responses(dataset, target_url)

    # 3. 加载 ID → 上下文文本
    id_index = _load_id_to_text_index()
    print(f"\n📚 加载 {len(id_index)} 个语义 ID → 上下文文本\n")

    # 4. 初始化 LLM 与指标
    async_client = AsyncOpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com/v1",
    )
    # Faithfulness 需要输出较长的 statement 列表，加大 max_tokens
    evaluator_llm = llm_factory(
        model="deepseek-chat",
        provider="openai",
        client=async_client,
        max_tokens=8000,   # 默认值太小，导致 Faithfulness 被截断
    )
    ragas_emb = HuggingFaceEmbeddings(
        model="thenlper/gte-small-zh",
        cache_folder=os.path.join(BASE_DIR, "uploads", "models"),
    )

    metrics = {
        "faithfulness": Faithfulness(llm=evaluator_llm),
        "answer_relevancy": AnswerRelevancy(llm=evaluator_llm, embeddings=ragas_emb),
        "context_precision": ContextPrecision(llm=evaluator_llm),
        "context_recall": ContextRecall(llm=evaluator_llm),
    }

    # 5. 逐条评估（并发 5）
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    tasks = [evaluate_one(s, id_index, metrics, sem) for s in samples]
    results = await asyncio.gather(*tasks)

    # 6. 输出报告
    print("\n=== RAGAS 评估报告 ===")
    metric_cols = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    averages = {}
    for col in metric_cols:
        vals = [r[col] for r in results if r.get(col) is not None]
        if vals:
            avg = sum(vals) / len(vals)
            averages[col] = avg
            print(f"{col:25s}: {avg:.4f}  (n={len(vals)})")
        else:
            print(f"{col:25s}: N/A")

    # 7. 保存 CSV
    import csv
    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"ragas_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["question", "answer"] + metric_cols)
        writer.writeheader()
        for r in results:
            writer.writerow(r)
    print(f"\n📄 详细报告已保存至 {output_path}")


if __name__ == "__main__":
    asyncio.run(main())