import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.rag_v2 import search_knowledge_v2

# ================= 内置测试集（请确保知识库已上传对应文档！） =================
TEST_CASES = [
    {"id": "1", "question": "打印机报错 0x80004005 怎么处理", "keywords": ["0x80004005", "权限"], "source_doc": "打印机故障排查手册"},
    {"id": "2", "question": "我的电脑开不了机，是不是坏了？", "keywords": ["开不了机", "无法开机"], "source_doc": "ThinkPad 硬件维修手册"},
    {"id": "3", "question": "ThinkPad X1 2024 无法启动屏幕黑屏怎么办", "keywords": ["ThinkPad X1 2024", "黑屏"], "source_doc": "ThinkPad 硬件维修手册"},
    {"id": "4", "question": "帮我算算2024年7月份的咖啡销售收入是多少？", "keywords": [], "source_doc": "咖啡销售数据", "expected_type": "calculation"},
    {"id": "5", "question": "打印不出东西了怎么办", "keywords": ["打印不了", "无法打印"], "source_doc": "打印机故障排查手册"},
    {"id": "6", "question": "今天中午吃什么好呢？", "keywords": [], "source_doc": None, "expected_type": "negative"}
]

def run_evaluation():
    total = 0
    passed = 0
    print("===== 开始 V2 混合检索产线级压力测试 =====")

    for case in TEST_CASES:
        total += 1
        q = case["question"]
        expected_type = case.get("expected_type", "retrieval")

        # 1. 物理计算类问题（检查是否正确识别为计算类，未误触发 RAG）
        if expected_type == "calculation":
            result = search_knowledge_v2(q, "")
            if not result:
                passed += 1
                print(f"✅ [通过] {q} (已正确识别为计算类，未误触发RAG)")
            else:
                print(f"❌ [失败] {q} (错误触发了RAG，应为计算)")
            continue

        # 2. 负向测试（防幻觉能力）
        if expected_type == "negative":
            result = search_knowledge_v2(q, "")
            if not result:
                passed += 1
                print(f"✅ [通过] {q} (正确识别为无数据，未产生幻觉)")
            else:
                print(f"❌ [失败] {q} (产生了幻觉)")
            continue

        # 3. 知识库检索类（BM25 或 向量）
        result = search_knowledge_v2(q, "")
        
        # 判断是否检出了正确的文档（检查是否包含关键词）
        score = 0
        if any(k in result for k in case.get("keywords", [])):
            score += 1
        
        if score >= 1 and result:
            passed += 1
            print(f"✅ [通过] {q}")
        else:
            print(f"❌ [失败] {q} | 未检索到包含关键词: {case.get('keywords', [])} 的文档")

    accuracy = (passed / total) * 100 if total > 0 else 0
    print(f"\n===== 评估完成：{total} 条，通过 {passed} 条，综合召回率/准确率：{accuracy:.2f}% =====")

if __name__ == "__main__":
    run_evaluation()