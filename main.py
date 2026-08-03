import os, json
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))


from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
from memory import memory
#import rag
try:
    import rag
except ImportError:
    rag = None
from tools import TOOLS_METADATA, AVAILABLE_TOOLS
from guardrails import input_guard, tool_call_guard, output_guard
from pending_tools import pending
import asyncio
from agents import WorkerAgent



app = FastAPI()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

@app.on_event("startup")
async def startup_event():
    # 启动 Worker 的后台循环，使其持续监听任务队列
    asyncio.create_task(worker.run_loop())

SYSTEM_PROMPT = """
你是一个全能的AI助手，可以使用记忆、知识库和多种工具来回答用户问题。
【重要规则】如果对话历史或参考文档中已经包含用户所需的信息，请直接基于这些信息回答，严禁重复调用工具！仅在信息不足时才调用工具。
可用工具：
- get_current_time: 获取当前时间
- calculator: 数学计算
- query_database: 查询员工数据库
- send_email: 发送邮件
- web_search: 搜索互联网获取最新信息
- execute_python: 执行Python代码进行计算或数据处理
- analyze_file: 分析CSV/Excel文件
- generate_image: 根据文字描述生成图片，返回图片链接

当用户询问实时信息（如新闻、股价、天气）时，请调用 web_search。
当用户要求计算或数据分析时，可调用 execute_python 执行代码。
所有工具调用结果会返回给你，你据此生成最终回答。
【参考文档】：
{context}
"""

# 初始化通用执行智能体 (Worker)，挂载所有工具（除邮件外））
worker_tools = {
    "query_database": query_database,
    "analyze_file": analyze_file,
    "execute_python": execute_python,
    "web_search": web_search,
    "get_current_time": get_current_time,
    "calculator": calculator,
    # send_email 仍由 Conductor 直接处理或单独 Worker
}
worker = WorkerAgent("Worker", worker_tools)
    

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

# 核心聊天逻辑
async def chat_core(session_id: str, query: str, image_base64: str = None):
    print(f"[DEBUG] 收到请求: session_id={session_id}, query={query[:50]}...")
    print(f"[DEBUG] 当前 pending keys: {list(pending.keys())}")
    
    # 惰性启动 Worker 后台循环（仅首次调用时启动）
    if not worker.is_running:
        asyncio.create_task(worker.run_loop())

    # 0. 输入护栏
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg

    # 1. 检查是否为二次确认的确认回复
    if session_id in pending and "确认" in query.strip():
        print(f"[确认] 执行工具: {pending[session_id]['tool_name']}")
        tool_info = pending.pop(session_id)
        tool_name = tool_info["tool_name"]
        arguments = tool_info["arguments"]
        if tool_name in AVAILABLE_TOOLS:
            try:
                result = AVAILABLE_TOOLS[tool_name](**arguments)
                print(f"[确认] 工具返回: {result[:50]}...")
            except Exception as e:
                result = f"工具执行错误: {e}"
        else:
            result = f"未找到工具 {tool_name}"
        memory.append(session_id, "确认执行工具", result)
        return output_guard(result)

    # 2. 获取历史与知识库上下文
    history = memory.get(session_id)
    #context = rag.search_similar(query, k=3)
    if rag is not None:
        context = rag.search_similar(query, k=3)
    else:
        context = "暂无相关文档（知识库未加载）"
    system_content = SYSTEM_PROMPT.format(context=context if context else "暂无相关文档")

    # 3. 构建初始消息
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)

    if image_base64:
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

    # 4. 工具调用循环（多智能体调度版）
    for _ in range(8):   # 增加循环次数，适应异步分派
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

                # 处理危险工具（邮件），保持原有二次确认逻辑
                if func_name == "send_email":
                    if tool_call_guard(func_name):
                        pending[session_id] = {
                            "tool_name": func_name,
                            "arguments": arguments
                        }
                        confirm_msg = (
                            f"⚠️ 危险操作确认\n"
                            f"工具：{func_name}\n"
                            f"参数：{arguments}\n\n"
                            f"请回复 **“确认”** 以执行，或回复其他内容取消。"
                        )
                        return confirm_msg
                    try:
                        result = AVAILABLE_TOOLS[func_name](**arguments)
                    except Exception as e:
                        result = f"工具执行错误: {e}"
                # 其他工具通过 Worker 异步分派
                elif func_name in worker.tools:
                    task = {"tool": func_name, "arguments": arguments}
                    res = await worker.send_task(task)
                    if "error" in res:
                        result = f"工具执行错误: {res['error']}"
                    else:
                        result = res["result"]
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

    # 5. 更新记忆前先进行输出脱敏
    answer = output_guard(answer)
    memory.append(session_id, query, answer)

    return answer

import sqlite3, os

def init_database():
    """如果 sample.db 不存在，自动创建并插入示例数据"""
    db_path = "sample.db"
    if not os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT,
                position TEXT,
                salary INTEGER
            )
        ''')
        sample_data = [
            (1, "张三", "工程师", 60000),
            (2, "李四", "产品经理", 75000),
            (3, "王五", "设计师", 55000),
            (4, "赵六", "数据分析师", 68000),
        ]
        cursor.executemany("INSERT OR REPLACE INTO employees VALUES (?,?,?,?)", sample_data)
        conn.commit()
        conn.close()
        print("数据库 sample.db 已自动创建并插入示例数据。")


if __name__ == "__main__":
    init_database()   # 确保数据库存在
    import os
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)