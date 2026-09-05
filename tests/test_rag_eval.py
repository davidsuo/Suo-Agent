import sys, os
import argparse

# 【核心修复】无论脚本放在哪里，都能找到项目的根目录，并加入系统路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# 测试数据
TEST_CASES = [
    {"question": "2024年10月份的咖啡销售收入是多少？", "answer_contains": "2024/10/"},
    {"question": "2024年4月份的咖啡销售收入是多少？", "answer_contains": "2024/4/"},
    {"question": "2024年11月份的咖啡销售收入是多少？", "answer_contains": "2024/11/"},
    {"question": "2024年12月份的咖啡销售收入是多少？", "answer_contains": "2024/12/"}
]

def get_retrieval_function(mode):
    """根据模式获取对应的检索函数"""
    if mode == 'v2':
        from common.rag_v2 import search_knowledge_v2
        return search_knowledge_v2
    else:
        from common.rag import search_knowledge
        return search_knowledge

def run_evaluation(mode='v1'):
    total = 0
    passed = 0
    print(f"==== 开始 RAG {mode.upper()} 模式持续评估跑分（按日期前缀验证） ====")
    
    search_func = get_retrieval_function(mode)
    
    for case in TEST_CASES:
        total += 1
        query = case["question"]
        result = ""
        
        # V1 模式需要传 session 和 tags；V2 模式当前直接传 query
        if mode == 'v1':
            result = search_func(query, "test_session", "销售，市场部")
            if not result:
                result = search_func(query, "test_session", "")
        else:
            result = search_func(query, "")
        
        check_passed = False
        if result and case["answer_contains"] in result:
            check_passed = True
        
        if check_passed:
            passed += 1
            print(f"✅ [通过] 问题: {query} | 命中 {case['answer_contains']}")
        else:
            print(f"❌ [失败] 问题: {query} | 未包含 {case['answer_contains']}")
    
    accuracy = (passed / total) * 100 if total > 0 else 0
    print(f"\n==== 评估完成: {mode.upper()} 模式 | 总测试集 {total} 条，通过 {passed} 条，准确率 {accuracy:.2f}% ====\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行RAG持续评估脚本")
    parser.add_argument('--mode', type=str, choices=['v1', 'v2'], default='v1', help="指定RAG模式（默认v1）")
    args = parser.parse_args()
    
    run_evaluation(args.mode)