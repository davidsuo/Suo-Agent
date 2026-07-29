import os, json
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

from memory import memory
import rag
from tools import TOOLS_METADATA, AVAILABLE_TOOLS   # 新增导入

app = FastAPI()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """
你是一个乐于助人的AI助手，可以使用多种工具和知识库来回答用户问题。
- 若需实时时间，使用 get_current_time。
- 若需计算，使用 calculator。
- 若需查询员工信息（例如工资、职位），使用 query_database。数据库包含 employees 表，字段：id, name, position, salary。
- 若用户要求发送邮件，使用 send_email。发件前请确认收件人、主题和内容。
- 如果有参考文档，优先基于文档回答。
- 如果以上都无法回答，诚实地说不知道。
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
    <head><title>AI Agent with Tools</title></head>
    <body>
        <h2>AI 智能体（记忆 + 知识库 + 工具）</h2>
        <label>会话ID: <input type="text" id="session_id" value="default"></label>
        <br><br>
        <input type="text" id="query" placeholder="试试问：现在几点？或计算(123+456)*7" size="50">
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
    # 1. 获取历史 & 知识库上下文
    history = memory.get(request.session_id)
    context = rag.search_similar(request.query, k=3)
    system_content = SYSTEM_PROMPT.format(context=context if context else "暂无相关文档")

    # 2. 构建初始 messages
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": request.query})

    # 3. 工具调用循环（最多5轮，防止死循环）
    for _ in range(5):
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOLS_METADATA,
            tool_choice="auto"   # 让模型自己决定是否调用工具
        )
        msg = response.choices[0].message

        # 如果模型想调用工具
        if msg.tool_calls:
            # 把模型的工具调用请求加入对话
            messages.append(msg)

            for tool_call in msg.tool_calls:
                func_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                print(f"[Tool] Calling {func_name} with {arguments}")

                # 执行工具函数
                if func_name in AVAILABLE_TOOLS:
                    try:
                        result = AVAILABLE_TOOLS[func_name](**arguments)
                    except Exception as e:
                        result = f"工具执行错误: {e}"
                else:
                    result = f"未找到工具 {func_name}"

                # 将工具执行结果作为一条消息加入对话
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })
            # 继续循环，让模型基于工具结果生成回复
            continue
        else:
            # 模型直接生成文本回复，停止循环
            answer = msg.content
            break
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # 4. 更新记忆
    memory.append(request.session_id, request.query, answer)

    return {"answer": answer}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)