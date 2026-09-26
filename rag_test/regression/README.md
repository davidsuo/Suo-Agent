# 回归测试

## 前置
1. 本地后端已启动：`uvicorn common.main:app --port 10000`
2. users.db 里 4 个账号角色正确

## 运行
```bash
python rag_test/regression/run_all.py            # 本地
python rag_test/regression/run_all.py --cloud    # 云端
python rag_test/regression/run_all.py --only permissions