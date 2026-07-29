import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

from memory import memory      # 对话记忆
import rag                     # 知识库检索

app = FastAPI()

# DeepSeek 客户端
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """
你是一个乐于助人的AI助手。请根据提供的【参考文档】回答用户问题。
如果文档中没有相关信息，请礼貌地说明你不知道，不要编造。
【参考文档】：
{context}
"""

class ChatRequest(BaseModel):
    session_id: str = "default"
    query: str

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>AI Chat with RAG + Memory</title></head>
    <body>
        <h2>AI 聊天原型（记忆 + 知识库）</h2>
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
    # 1. 获取历史对话
    history = memory.get(request.session_id)

    # 2. 检索相关文档片段（RAG 核心）
    context = rag.search_similar(request.query, k=3)

    # 3. 构建 messages：system prompt（含文档上下文） + 历史消息 + 当前问题
    system_content = SYSTEM_PROMPT.format(context=context if context else "暂无相关文档")
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": request.query})

    # 4. 调用 LLM
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages
    )
    answer = resp.choices[0].message.content

    # 5. 更新记忆
    memory.append(request.session_id, request.query, answer)

    return {"answer": answer}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)