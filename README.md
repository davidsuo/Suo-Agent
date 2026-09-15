$msg = @"
Sprint 2: 分层RAG + 按月切分 + 临时文件隔离 + 语义驱动

【架构级改进】
- 分层索引：Markdown 走 GTE 向量+BM25；CSV 走纯 BM25
- 按月切分：13块/月，中文导航标注
- 临时文件隔离：聊天框上传 → uploads/temp
- 工具隐式参数：analyze_data 的 file_path 后端自动注入
- 事实陈述替代 prompt 约束

【修复】
- chat_core 所有 return 统一为 (answer, ids) 元组
- loadHistory 支持新格式
- 前端气泡不再刷屏
- 恢复知识库文档编辑标签功能

【已验证】
- 2024年10月咖啡销售 → 1389.12
- HP打印机报错0x80004005 → IT-01
- 临时文件路径正确注入 analyze_data
"@

git commit -m $msg