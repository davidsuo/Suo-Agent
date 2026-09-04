import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from common.rag_v2 import index_document_v2, search_knowledge_v2

# 1. 定义测试文档路径
test_file = "coffee_sales.csv"

print("==== 开始测试 RAG V2 数据接入与感知分块 ====")

# 2. 测试索引入库
print(f"\n正在索引文件: {test_file} ...")
result = index_document_v2(test_file, tags="2024年销售数据")
print(f"【索引结果】: {result}")

# 3. 测试语义检索（添加了元数据过滤）
print(f"\n正在检索: 2024年10月份的咖啡销售数据 (限定标签: 2024年销售数据) ...")
search_result = search_knowledge_v2("2024年10月份的咖啡销售数据", tags="2024年销售数据")
print(f"【检索结果片段】: \n{search_result[:500] if search_result else '（未检索到相关片段）'}")

print("\n==== 测试完成 ====")