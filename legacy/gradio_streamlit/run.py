# run.py
import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    # 使用 uvicorn 启动 FastAPI 应用，python run.py 会自动把当前目录加入路径
    uvicorn.run("common.main:app", host="0.0.0.0", port=port)