# migrate_v1_to_v2.py
# 将旧版 JSON 知识库数据迁移到 ChromaDB (V2)

import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from common.rag_v2 import index_document_v2

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_DATA_FILE = os.path.join(BASE_DIR, "rag_data.json")

def migrate():
    if not os.path.exists(RAG_DATA_FILE):
        print("❌ 未找到 rag_data.json，无需迁移。")
        return

    with open(RAG_DATA_FILE, "r", encoding="utf-8") as f:
        store = json.load(f)

    # 遍历所有标签下的文档
    for key, docs in store.get("store", {}).items():
        for doc in docs:
            file_name = doc.get("file_name", "未知")
            text_content = doc.get("text", "")
            tag = doc.get("tags", "")

            if not text_content:
                continue

            print(f"正在迁移文档: {file_name} (标签: {tag}) ...")
            # 利用V2的索引函数，传标签和内容
            try:
                # V2需要文件路径，为了兼容，我们创建一个临时txt来迁移
                temp_path = os.path.join(BASE_DIR, "temp_migrate.txt")
                with open(temp_path, "w", encoding="utf-8") as f:
                    f.write(text_content)
                
                result = index_document_v2(temp_path, tags=tag)
                print(f"✅ 迁移结果: {result}")
            except Exception as e:
                print(f"❌ 迁移失败: {e}")

    print("\n==== 迁移完成，请运行测试验证 ====")

if __name__ == "__main__":
    migrate()