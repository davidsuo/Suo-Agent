# rag_test/minimal_ragas_test.py
import os
import asyncio
import warnings
from dotenv import load_dotenv

load_dotenv()
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

# 1. 从环境变量读取 DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 2. 创建异步 OpenAI 客户端
async_client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

# 3. 用 llm_factory 包装成 RAGAS 的 LLM
#    关键：显式指定 provider="openai"，让 RAGAS 使用 OpenAI 兼容协议
evaluator_llm = llm_factory(
    model="deepseek-chat",
    provider="openai",
    client=async_client,
)

# 4. Embeddings 用于 AnswerRelevancy
ragas_emb = HuggingFaceEmbeddings(
    model="thenlper/gte-small-zh",
    cache_folder=os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "models"),
)

# 5. 准备测试数据（单条）
user_input = "打印机报错 0x80004005 怎么处理"
response = "0x80004005 是 HP 打印机共享权限错误。解决：1. 控制面板删除驱动；2. 重启电脑和打印机；3. 确保同一网段；4. 重新添加并共享。"
retrieved_contexts = [
    "[IT-01] 0x80004005 是 HP 打印机最常见的共享权限错误，通常由局域网发现失败、驱动冲突或打印队列卡死导致。处理：删除驱动、重启设备、确保同网段、重新添加。"
]
reference = "0x80004005 是 HP 打印机共享权限错误。删除驱动，重启电脑和打印机，确保同一网段，重新添加并共享。"


async def main():
    # 6. 实例化指标
    faithfulness_metric = Faithfulness(llm=evaluator_llm)
    answer_relevancy_metric = AnswerRelevancy(llm=evaluator_llm, embeddings=ragas_emb)
    context_precision_metric = ContextPrecision(llm=evaluator_llm)
    context_recall_metric = ContextRecall(llm=evaluator_llm)

    print("开始计算 Faithfulness ...")
    faith_result = await faithfulness_metric.ascore(
        user_input=user_input,
        response=response,
        retrieved_contexts=retrieved_contexts,
    )
    print(f"Faithfulness: {faith_result.value}")

    print("\n开始计算 Answer Relevancy ...")
    relevancy_result = await answer_relevancy_metric.ascore(
        user_input=user_input,
        response=response,
    )
    print(f"Answer Relevancy: {relevancy_result.value}")

    print("\n开始计算 Context Precision ...")
    precision_result = await context_precision_metric.ascore(
        user_input=user_input,
        retrieved_contexts=retrieved_contexts,
        reference=reference,
    )
    print(f"Context Precision: {precision_result.value}")

    print("\n开始计算 Context Recall ...")
    recall_result = await context_recall_metric.ascore(
        user_input=user_input,
        retrieved_contexts=retrieved_contexts,
        reference=reference,
    )
    print(f"Context Recall: {recall_result.value}")

    print("\n=== 评估完成 ===")
    print(f"Faithfulness:       {faith_result.value:.4f}")
    print(f"Answer Relevancy:   {relevancy_result.value:.4f}")
    print(f"Context Precision:  {precision_result.value:.4f}")
    print(f"Context Recall:     {recall_result.value:.4f}")


if __name__ == "__main__":
    asyncio.run(main())