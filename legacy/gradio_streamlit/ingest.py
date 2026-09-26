import rag

# 把 test.txt 导入知识库
num = rag.add_document_to_store("test.txt")
print(f"成功导入 {num} 个文本块")