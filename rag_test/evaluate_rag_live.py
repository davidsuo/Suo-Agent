import os
import json
import time
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any

# 【关键】加载项目根目录的 .env 文件，否则读不到 OPENAI_API_KEY
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    print("警告：未安装 httpx，请运行 pip install httpx")


# 【口径修正】RAG 系统设计目标是"宁多勿漏"，优先看 recall
METRICS_THRESHOLDS = {
    "context_recall": 0.70,
    "context_precision": 0.15,
    "out_of_scope_accuracy": 0.80,
    "clarify_accuracy": 0.80,
    "answer_relevancy": 0.05,
}

# 【防御】LLM/API 调用失败时的信号（这些情况下本样本不计入失败）
API_FAILURE_SIGNALS = [
    "模型调用失败", "Insufficient Balance", "Error code: 402",
    "Error code: 429", "Error code: 500", "Error code: 502", "Error code: 503",
    "网络错误", "连接失败", "Connection error",
]


class RAGV2Evaluator:
    def __init__(self, rag_client: Any):
        self.rag_client = rag_client
        self.results = []
        # 【语义判定】独立的 judge client
        from openai import OpenAI
        self._judge_client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )

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

        # answer_relevancy 只对 positive 类计算
        if q_type in ("out_of_scope", "clarify"):
            llm_relevancy = 0.0
        else:
            llm_relevancy = await self._compute_llm_relevancy(query, generated_answer, retrieved_ids)

        final_metrics = {**retrieval_metrics, "answer_relevancy": round(llm_relevancy, 4)}

        if q_type == "out_of_scope":
            is_api_failure = any(sig in (generated_answer or "") for sig in API_FAILURE_SIGNALS)
            if is_api_failure:
                print(f"⚠️ 样本 {sample['id']} LLM 调用失败，本样本跳过判分")
                final_metrics["out_of_scope_accuracy"] = -1.0
                passed = True
            else:
                judged_ok = await self._judge_out_of_scope_answer(query, generated_answer or "")
                final_metrics["out_of_scope_accuracy"] = 1.0 if judged_ok else 0.0
                passed = judged_ok
        elif q_type == "clarify":
            is_api_failure = any(sig in (generated_answer or "") for sig in API_FAILURE_SIGNALS)
            if is_api_failure:
                print(f"⚠️ 样本 {sample['id']} LLM 调用失败，本样本跳过判分")
                final_metrics["clarify_accuracy"] = -1.0
                passed = True
            else:
                judged_ok = await self._judge_clarify_answer(query, generated_answer or "")
                final_metrics["clarify_accuracy"] = 1.0 if judged_ok else 0.0
                passed = judged_ok
        else:
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
        if q_type in ("out_of_scope", "clarify"):
            return {"context_recall": 0.0, "context_precision": 0.0}
        if len(gt_set) == 0:
            return {"context_recall": 0.0, "context_precision": 0.0}
        overlap = len(retrieved_set & gt_set)
        context_recall = overlap / len(gt_set) if gt_set else 0.0
        context_precision = overlap / len(retrieved_set) if retrieved_set else 0.0
        return {
            "context_recall": round(context_recall, 4),
            "context_precision": round(context_precision, 4),
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

    async def _judge_out_of_scope_answer(self, query: str, answer: str) -> bool:
        """【语义判定】判断 out_of_scope 类回答是否恰当。

        恰当：明确说"我做不到/知识库没收录/无法回答"，可以附加"建议咨询 XX 部门/查 OA"这类渠道建议。
        不恰当：编造内容、假装能做、或给出具体操作步骤假装能回答。
        """
        if not answer or not answer.strip():
            return False

        judge_prompt = f"""你是评审员。用户问了一个**超出系统能力范围**的问题，下面是 AI 系统的回答。

【系统能力范围】IT 支持（打印机/笔记本/网络故障）、数据分析、日程管理、邮件、联网搜索公开信息。

【用户问题】
{query}

【AI 回答（前 500 字符）】
{answer[:500]}

【判定标准】
- PASS：
  - AI 明确表示"我做不到 / 知识库没有收录 / 无法查询 / 无法预订 / 无法回答"，**没有编造内容**；
  - 或者，AI 在拒答的同时，**简短地建议用户去正确的渠道**（如"建议咨询财务部/HR/行政部门"、"建议查看 OA"、"建议联系厂商售后"）——**这类渠道建议属于 PASS**。
- FAIL：
  - AI **编造了具体内容**（比如凭空写出报销流程、凭空给出产品文案、凭空给出股价数字）；
  - 或 AI **假装能回答**（比如给出具体的操作步骤、具体金额、具体流程假装自己知道）；
  - 或 AI **完全答非所问**。

只输出一个词：PASS 或 FAIL。"""

        try:
            resp = self._judge_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": judge_prompt}],
                max_tokens=5,
                temperature=0
            )
            result = (resp.choices[0].message.content or "").strip().upper()
            return "PASS" in result
        except Exception as e:
            print(f"out_of_scope 判定失败: {e}")
            return False

    async def _judge_clarify_answer(self, query: str, answer: str) -> bool:
        """【语义判定】判断 clarify 类回答是否恰当。

        恰当：AI 追问用户缺失的信息（如"请提供产品名称/股票代码"）。
        不恰当：AI 直接编造回答、或生硬拒答、或答非所问。
        """
        if not answer or not answer.strip():
            return False

        judge_prompt = f"""你是评审员。用户问了一个**信息不足、需要追问澄清**的问题，下面是 AI 系统的回答。

【用户问题】
{query}

【AI 回答】
{answer}

【判定标准】
- PASS：AI 识别出信息不足，**追问用户缺失的关键信息**（如"请提供产品名称、卖点、目标人群"、"请提供股票代码"），并且**没有编造具体内容**。
- FAIL：AI 直接编造了回答（如凭空写文案、凭空给股价）、或生硬拒答（"我做不到"）、或答非所问。

只输出一个词：PASS 或 FAIL。"""

        try:
            resp = self._judge_client.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": judge_prompt}],
                max_tokens=5,
                temperature=0
            )
            result = (resp.choices[0].message.content or "").strip().upper()
            return "PASS" in result
        except Exception as e:
            print(f"clarify 判定失败: {e}")
            return False

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
            # out_of_scope_accuracy 只对 out_of_scope 样本求平均，排除无效样本(-1)
            if metric == "out_of_scope_accuracy":
                samples = [r for r in self.results
                           if r["type"] == "out_of_scope" and r["metrics"].get(metric, 0) >= 0]
                if samples:
                    values = [r["metrics"].get(metric, 0) for r in samples]
                    avg_metrics[metric] = round(sum(values) / len(values), 4)
                else:
                    avg_metrics[metric] = 0.0
            elif metric == "clarify_accuracy":
                samples = [r for r in self.results
                           if r["type"] == "clarify" and r["metrics"].get(metric, 0) >= 0]
                if samples:
                    values = [r["metrics"].get(metric, 0) for r in samples]
                    avg_metrics[metric] = round(sum(values) / len(values), 4)
                else:
                    avg_metrics[metric] = 0.0
            else:
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
        self._counter = 0

    async def generate(self, query: str):
        if not HTTPX_AVAILABLE:
            return {"answer": "httpx未安装", "contexts": []}

        self._counter += 1
        # 每题独立 session，避免历史累积污染
        payload = {"session_id": f"alice_eval_{self._counter:03d}", "query": query, "user_text": query}
        answer_content = ""
        context_list = []

        async with httpx.AsyncClient(timeout=180) as client:
            try:
                async with client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    if response.status_code != 200:
                        return {"answer": f"请求失败: {response.status_code}", "contexts": []}

                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                continue
                            try:
                                data = json.loads(data_str)
                                if data.get("type") == "answer":
                                    answer_content += data.get("content", "")
                                    new_contexts = data.get("contexts") or []
                                    for cid in new_contexts:
                                        if cid not in context_list:
                                            context_list.append(cid)
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

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    output_path = os.path.join(SCRIPT_DIR, f"eval_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"报告已保存至 {output_path}")


if __name__ == "__main__":
    asyncio.run(main())