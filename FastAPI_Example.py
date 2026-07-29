from fastapi import FastAPI
from pydantic import BaseModel

# 实例化服务
app = FastAPI(title="演示接口服务")

# 定义请求体模型
class User(BaseModel):
    name: str
    age: int

# GET接口
@app.get("/")
def index():
    return {"msg": "欢迎使用FastAPI"}

# POST接收JSON
@app.post("/user")
def create_user(user: User):
    return {"data": user, "status": "success"}