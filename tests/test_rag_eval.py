import sys, os
# 【核心修复】无论脚本放在哪里，都能找到项目的根目录，并加入系统路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from common.rag import search_knowledge

# 测试数据直接写死在这里
TEST_CASES = [
    {"question": "2024年10月份的咖啡销售收入是多少？", "answer_contains": "2024/10/"},
    {"question": "2024年4月份的咖啡销售收入是多少？", "answer_contains": "2024/4/"},
    {"question": "2024年11月份的咖啡销售收入是多少？", "answer_contains": "2024/11/"},
    {"question": "现在几点了？", "answer_contains": "北京时间"},
    {"question": "2024年12月份的咖啡销售收入是多少？", "answer_contains": "2024/12/"}
]

def run_evaluation():
    total = 0
    passed = 0
    print("==== 开始 RAG 持续评估跑分（按日期前缀验证） ====")
    
    for case in TEST_CASES:
        total += 1
        query = case["question"]
        
        # 先指定标签查询，如果没找到再尝试全局搜索
        result = search_knowledge(query, "test_session", "销售，市场部")
        if not result:
            result = search_knowledge(query, "test_session", "")
        
        check_passed = False
        if result and case["answer_contains"] in result:
            check_passed = True
        
        if check_passed:
            passed += 1
            print(f"✅ [通过] 成功检索到包含 {case['answer_contains']} 的明细数据。")
        else:
            print(f"❌ [失败] 未找到包含 {case['answer_contains']} 的数据。")
    
    accuracy = (passed / total) * 100 if total > 0 else 0
    print(f"\n==== 评估完成: 总测试集 {total} 条，通过 {passed} 条，准确率 {accuracy:.2f}% ====")

if __name__ == "__main__":
    run_evaluation()