import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

# 导入记忆模块
from memory import memory

app = FastAPI()

# 初始化 DeepSeek 客户端
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = "你是一个乐于助人的AI助手。"

# ---------- 修改点：ChatRequest 增加 session_id ----------
class ChatRequest(BaseModel):
    session_id: str = "default"   # 会话ID，默认用 "default"
    query: str

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>AI Chat with Memory</title></head>
    <body>
        <h2>AI 聊天原型（带记忆）</h2>
        <label>会话ID: <input type="text" id="session_id" value="default"></label>
        <br><br>
        <input type="text" id="query" placeholder="输入你的问题" size="50">
        <button onclick="ask()">发送</button>
        <pre id="answer"></pre>
        <script>
            async function ask() {
                const sid = document.getElementById("session_id").value;
                const q = document.getElementById("query").value;
                const res = await fetch("/chat", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({session_id: sid, query: q})
                });
                const data = await res.json();
                document.getElementById("answer").innerText = data.answer;
            }
        </script>
    </body>
    </html>
    """

@app.post("/chat")
async def chat(request: ChatRequest):
    # 1. 获取该会话的历史对话
    history = memory.get(request.session_id)

    # 2. 构建 messages：system prompt + 历史消息 + 当前用户问题
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)          # 插入之前的对话
    messages.append({"role": "user", "content": request.query})

    # 3. 调用 LLM
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages
    )
    answer = resp.choices[0].message.content

    # 4. 更新记忆（存储本轮问答）
    memory.append(request.session_id, request.query, answer)

    return {"answer": answer}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)