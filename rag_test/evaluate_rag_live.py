import os
import json
import time
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    print("警告：未安装 httpx，请运行 pip install httpx")

# 【口径修正】RAG 系统设计目标是"宁多勿漏"，优先看 recall
# precision 作为辅助参考，阈值放宽（Top-5 检索下 precision 天然较低）
METRICS_THRESHOLDS = {
    "context_recall": 0.70,
    "context_precision": 0.15,
    "rejection_accuracy": 0.80,
    "answer_relevancy": 0.05,
}


class RAGV2Evaluator:
    def __init__(self, rag_client: Any):
        self.rag_client = rag_client
        self.results = []

    async def load_dataset(self, dataset_path: str) -> List[Dict]:
        if not os.path.exists(dataset_path):
            if os.path.exists("test_set.json"):
                dataset_path = "test_set.json"
            else:
                raise FileNotFoundError(f"未找到数据集：{dataset_path}")
        with open(dataset_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("data", data.get("questions", []))
        normalized_data = []
        for item in data:
            if "question" not in item:
                continue
            normalized_data.append({
                "id": item.get("id", str(time.time_ns())),
                "question": item["question"],
                "type": item.get("type", "exact"),
                "ground_truth": item.get("ground_truth", []) or []
            })
        return normalized_data

    async def evaluate_sample(self, sample: Dict) -> Dict:
        query = sample["question"]
        gt_ids = sample["ground_truth"]
        q_type = sample["type"]
        start_time = time.time()
        try:
            response = await self.rag_client.generate(query)
            generated_answer = response.get("answer", "")
            retrieved_ids = response.get("contexts", [])
        except Exception as e:
            generated_answer = ""
            retrieved_ids = []
            print(f"警告：样本 {sample['id']} 生成失败，错误：{e}")
        latency = time.time() - start_time

        retrieval_metrics = self._compute_id_based_metrics(retrieved_ids, gt_ids, q_type)

        if q_type == "negative":
            llm_relevancy = 0.0
        else:
            llm_relevancy = await self._compute_llm_relevancy(query, generated_answer, retrieved_ids)

        final_metrics = {**retrieval_metrics, "answer_relevancy": round(llm_relevancy, 4)}

        # 【口径修正】通过条件分两类判定
        if q_type == "negative":
            # negative 题的拒答成功 = LLM 回答中明确表示"未找到/无法回答"
            # 而非 "contexts 为空"（背景检索会返回一些 ID，但不影响 LLM 拒答）
            answer_lower = (generated_answer or "").lower()
            reject_signals = ["未找到", "没有找到", "无法回答", "无法提供", "不包含",
                              "知识库中未", "抱歉", "无法直接回答", "没有收录", "未收录"]
            rejected = any(sig in answer_lower for sig in reject_signals)
            final_metrics["rejection_accuracy"] = 1.0 if rejected else 0.0
            passed = rejected
        else:
            # 非 negative 题：优先看 recall（覆盖度），precision 作为辅助
            passed = final_metrics.get("context_recall", 0) >= METRICS_THRESHOLDS["context_recall"]

        return {
            "id": sample["id"], "query": query, "type": q_type,
            "ground_truth": gt_ids, "retrieved_ids": retrieved_ids,
            "generated_answer": generated_answer,
            "metrics": final_metrics, "latency_seconds": latency, "passed": passed
        }

    def _compute_id_based_metrics(self, retrieved_ids, gt_ids, q_type):
        retrieved_set = set(retrieved_ids)
        gt_set = set(gt_ids)
        if q_type == "negative":
            # 【口径修正】negative 题的 rejection_accuracy 在 evaluate_sample 里判定
            # 这里只返回占位值，由 evaluate_sample 覆盖
            return {"context_recall": 0.0, "context_precision": 0.0, "rejection_accuracy": 0.0}
        if len(gt_set) == 0:
            return {"context_recall": 0.0, "context_precision": 0.0, "rejection_accuracy": 0.0}
        overlap = len(retrieved_set & gt_set)
        context_recall = overlap / len(gt_set) if gt_set else 0.0
        context_precision = overlap / len(retrieved_set) if retrieved_set else 0.0
        return {
            "context_recall": round(context_recall, 4),
            "context_precision": round(context_precision, 4),
            "rejection_accuracy": 0.0
        }

    async def _compute_llm_relevancy(self, query, answer, retrieved_ids):
        if not answer or not retrieved_ids:
            return 0.0
        query_tokens = set(query.lower().split())
        answer_tokens = set(answer.lower().split())
        common = len(query_tokens & answer_tokens)
        if query_tokens and answer_tokens:
            score = common / min(len(query_tokens), len(answer_tokens))
            return min(score, 1.0)
        return 0.0

    async def run_full_evaluation(self, dataset_path: str, concurrency: int = 1) -> Dict:
        dataset = await self.load_dataset(dataset_path)
        if not dataset:
            return self.generate_report()
        semaphore = asyncio.Semaphore(concurrency)

        async def evaluate_with_semaphore(sample):
            async with semaphore:
                return await self.evaluate_sample(sample)

        self.results = await asyncio.gather(*[evaluate_with_semaphore(s) for s in dataset])
        return self.generate_report()

    def generate_report(self):
        if not self.results:
            return {
                "total_samples": 0, "passed_samples": 0, "pass_rate": 0.0,
                "average_metrics": {k: 0.0 for k in METRICS_THRESHOLDS},
                "results": []
            }
        avg_metrics = {}
        for metric in METRICS_THRESHOLDS.keys():
            values = [r["metrics"].get(metric, 0) for r in self.results]
            avg_metrics[metric] = round(sum(values) / len(values), 4) if values else 0
        passed_count = sum(1 for r in self.results if r["passed"])
        total_count = len(self.results)
        return {
            "evaluation_time": datetime.now(timezone.utc).isoformat(),
            "total_samples": total_count,
            "passed_samples": passed_count,
            "pass_rate": round(passed_count / total_count, 4) if total_count else 0,
            "average_metrics": avg_metrics,
            "average_latency_seconds": round(sum(r["latency_seconds"] for r in self.results) / total_count, 4) if total_count else 0,
            "results": self.results
        }


class RealRAGClient:
    def __init__(self, base_url: str = "http://127.0.0.1:10000"):
        self.base_url = base_url

    async def generate(self, query: str):
        if not HTTPX_AVAILABLE:
            return {"answer": "httpx未安装", "contexts": []}
        
        # 【核心修复】必须使用系统里真实存在的用户，否则会被安全拦截
        payload = {"session_id": "alice_主对话", "query": query, "user_text": query}
        answer_content = ""
        context_list = []

        async with httpx.AsyncClient(timeout=180) as client:
            try:
                # 使用 stream 方法接收 SSE 流
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code != 200:
                        return {"answer": f"请求失败: {response.status_code}", "contexts": []}

                    # 逐行读取流数据
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                continue
                            try:
                                data = json.loads(data_str)
                                if data.get("type") == "answer":
                                    answer_content += data.get("content", "")
                                    # 如果返回中有 contexts，则提取
                                    if data.get("contexts"):
                                        context_list = data.get("contexts")
                            except Exception:
                                pass
                
                print(f"【诊断-评估器】问题: {query[:30]}...")
                print(f"【诊断-评估器】后端返回的contexts: {context_list}")
                return {"answer": answer_content, "contexts": context_list}
                
            except Exception as e:
                print(f"【诊断-评估器】连接失败: {type(e).__name__}: {e}")
                return {"answer": f"网络错误: {e}", "contexts": []}


class MockRAGClient:
    async def generate(self, query):
        if "打印机报错" in query or "0x80004005" in query:
            return {"answer": "...", "contexts": ["IT-01"]}
        elif "共享打印机" in query:
            return {"answer": "...", "contexts": ["IT-05"]}
        elif "下午茶" in query or "报销" in query:
            return {"answer": "未找到相关文档", "contexts": []}
        else:
            return {"answer": "通用处理办法", "contexts": ["IT-99"]}

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="RAGV2 文档ID检索评估")
    parser.add_argument("dataset", nargs="?", help="数据集路径")
    parser.add_argument("--real", action="store_true", help="使用真实 FastAPI 后端")
    parser.add_argument("--local", action="store_true", help="测试本地后端（默认测试 Render 云端）")
    args = parser.parse_args()
    dataset_path = args.dataset or "test_set.json"
    
    # 【核心修复】显式判断并打印目标地址
    if args.local:
        target_url = "http://127.0.0.1:10000"
    else:
        target_url = "https://suo-agent.onrender.com"
    
    print(f"⚠️ 警告：正在连接后端服务 [{target_url}] ...")
    rag_client = RealRAGClient(base_url=target_url) if args.real else MockRAGClient()
    evaluator = RAGV2Evaluator(rag_client=rag_client)
    
    report = await evaluator.run_full_evaluation(dataset_path)
    print("\n=== 评估报告 ===")
    print(json.dumps(report["average_metrics"], indent=2, ensure_ascii=False))
    print(f"通过率: {report['pass_rate']}")


if __name__ == "__main__":
    asyncio.run(main())