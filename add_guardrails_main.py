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
from guardrails import input_guard, tool_call_guard, output_guard


app = FastAPI()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """
你是一个全能的AI助手，可以使用记忆、知识库和多种工具来回答用户问题。
可用工具：
- get_current_time: 获取当前时间
- calculator: 数学计算
- query_database: 查询员工数据库
- send_email: 发送邮件
- web_search: 搜索互联网获取最新信息
- execute_python: 执行Python代码进行计算或数据处理

当用户询问实时信息（如新闻、股价、天气）时，请调用 web_search。
当用户要求计算或数据分析时，可调用 execute_python 执行代码。
如果 web_search 返回的结果包含“(实时搜索暂时不可用)”，请在回答中首先说明搜索服务暂时受限，然后根据提供的模拟信息给出参考
所有工具调用结果会返回给你，你据此生成最终回答。
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
    answer = await chat_core(request.session_id, request.query)
    return {"answer": answer}

# main.py 内部新增函数（放在所有 @app 路由之后）
async def chat_core(session_id: str, query: str, image_base64: str = None):

    # 0. 输入护栏
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg

    """
    聊天核心逻辑：记忆+RAG+工具调用，返回回答。
    新增 image_base64 参数，为多模态预留。
    """
    # 1. 历史 & 上下文
    history = memory.get(session_id)
    context = rag.search_similar(query, k=3)
    system_content = SYSTEM_PROMPT.format(context=context if context else "暂无相关文档")

    # 2. 构建初始消息
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)

    # 如果有图片，构建多模态消息（取决于模型是否支持）
    if image_base64:
        # DeepSeek Chat 目前可能不支持 vision，这里仅作示例
        user_message = {
            "role": "user",
            "content": [
                {"type": "text", "text": query},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
            ]
        }
    else:
        user_message = {"role": "user", "content": query}
    messages.append(user_message)

    # 3. 工具调用循环
    for _ in range(5):
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            tools=TOOLS_METADATA,
            tool_choice="auto"
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            messages.append(msg)
            for tool_call in msg.tool_calls:
                func_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)

                if func_name in AVAILABLE_TOOLS:
                    # 危险工具二次确认
                    if tool_call_guard(func_name):
                        return "⚠️ 危险操作：即将调用 {}，请确认您确实要执行此操作。".format(func_name)
                    try:
                        result = AVAILABLE_TOOLS[func_name](**arguments)
                    except Exception as e:
                        result = f"工具执行错误: {e}"
                else:
                    result = f"未找到工具 {func_name}"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result
                })
            continue
        else:
            answer = msg.content
            break
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # 4. 更新记忆
    memory.append(session_id, query, answer)

    # 在 chat_core 函数最后，return answer 之前
    answer = output_guard(answer)
    # 如果有记忆更新，也脱敏
    memory.append(session_id, query, answer)

    return answer


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)