import os, json, asyncio, time, uuid, traceback
import re

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

from memory import memory

try:
    import rag
except ImportError:
    rag = None

from tools import (
    TOOLS_METADATA, AVAILABLE_TOOLS,
    get_current_time, calculator,
    query_database, web_search, execute_python,
    speech_to_text, analyze_file,
    fetch_webpage, generate_image,
    ocr_image,
    add_event, list_events, delete_event,
    recognize_table,
    send_email,
    COMPENSATIONS,
)
from guardrails import input_guard, tool_call_guard, output_guard
from pending_tools import pending, save_pending

# 恢复内存总线
#from event_bus import EventBus
#bus = EventBus

# Redis消息总线
from redis_bus import RedisEventBus
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL:
    raise ValueError("REDIS_URL 环境变量未设置")

bus = RedisEventBus(REDIS_URL)

from agents import WorkerAgent, QueryWorker

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
- analyze_file: 分析CSV/Excel文件
- fetch_webpage: 抓取网页全文
- generate_image: 生成图片
- ocr_image: 图片文字识别
- recognize_table: 表格识别
- add_event: 添加日程
- list_events: 列出日程
- delete_event: 删除日程

当用户询问实时信息时，请调用 web_search。
当用户要求计算或数据分析时，可调用 execute_python 执行代码。
如果 web_search 返回的结果包含“(实时搜索暂时不可用)”，请在回答中首先说明搜索服务暂时受限，然后根据提供的模拟信息给出参考。
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

# ========== 查询类 Worker（带缓存） ==========
query_worker_tools = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "list_events": list_events,
    "web_search": web_search,
    "fetch_webpage": fetch_webpage,
    "ocr_image": ocr_image,
    "recognize_table": recognize_table,
    "analyze_file": analyze_file,
    "speech_to_text": speech_to_text,
}
query_worker = QueryWorker("QueryWorker", query_worker_tools, bus)

# ========== 命令类 Worker ==========
command_worker_tools = {
    "send_email": send_email,
    "add_event": add_event,
    "delete_event": delete_event,
    "execute_python": execute_python,
    "generate_image": generate_image,
}
command_worker = WorkerAgent("CommandWorker", command_worker_tools, bus)

# 工具路由表（直接映射到 Worker 实例）
TOOL_ROUTER = {}
for name in query_worker.tools:
    TOOL_ROUTER[name] = query_worker
for name in command_worker.tools:
    TOOL_ROUTER[name] = command_worker

# 监控用列表
ALL_WORKERS = [query_worker, command_worker]

def get_workers_status():
    return [w.get_stats() for w in ALL_WORKERS]
    
async def send_task_via_bus(worker_name: str, task: dict, timeout: int = 60):
    """通过 Redis 总线发送任务并等待结果"""
    future = asyncio.get_event_loop().create_future()
    event_data = {"task": task, "future": future}
    await bus.publish(f"ToolRequested.{worker_name}", event_data)
    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": "任务超时"}

# ========== 核心聊天逻辑 ==========
async def chat_core(session_id: str, query: str, image_base64: str = None):
    print(f"[DEBUG] 收到请求: session_id={session_id}, query={query[:50]}...")
    print(f"[DEBUG] 当前 pending keys: {list(pending.keys())}")

    # 0. 输入护栏
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg

    # 惰性启动所有专业 Worker（仅首次调用时执行）
    if not query_worker.is_running:
        asyncio.create_task(query_worker.run_loop())
        query_worker.is_running = True
    if not command_worker.is_running:
        asyncio.create_task(command_worker.run_loop())
        command_worker.is_running = True
    print("Workers ready: QueryWorker, CommandWorker")

    # 1. 检查是否为二次确认的确认回复
    if session_id in pending and "确认" in query.strip():
        print(f"[确认] 执行待处理工具 for session {session_id}")
        tool_info = pending.pop(session_id)
        save_pending(pending)
        tool_name = tool_info["tool_name"]
        arguments = tool_info["arguments"]
        if tool_name in AVAILABLE_TOOLS:
            try:
                result = AVAILABLE_TOOLS[tool_name](**arguments)
            except Exception as e:
                result = f"工具执行错误: {e}"
        else:
            result = f"未找到工具 {tool_name}"
        memory.append(session_id, "确认执行工具", result)
        return output_guard(result)

    # 2. 获取历史与知识库上下文
    history = memory.get(session_id)
    context = rag.search_similar(query, k=3) if rag is not None else "暂无相关文档（知识库未加载）"
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

    # 4. 尝试生成任务计划
    plan = None
    try:
        plan = await generate_plan(query, messages[:5], client)
        if plan and len(plan) > 1:
            print(f"[规划引擎] 生成计划，共 {len(plan)} 步")
        else:
            plan = None
            print("[规划引擎] 无法生成有效计划，回退到常规工具调用模式")
    except Exception as e:
        print(f"[规划引擎] 生成计划异常: {e}，回退到常规模式")
        plan = None

    image_output = None

    if plan:
        # ========== 规划引擎执行模式（Saga 补偿版） ==========
        results = {}
        email_args = None
        completed_steps = []

        for step in plan:
            step_id = step["id"]
            tool_name = step["tool"]
            arguments = step["arguments"]
            arguments["_tenant"] = memory.get_tenant(session_id)

            for dep_id in step.get("depends_on", []):
                if dep_id in results:
                    replacement = str(results[dep_id])
                    for key, val in arguments.items():
                        if isinstance(val, str):
                            arguments[key] = val.replace(f"{{step_{dep_id}_result}}", replacement)

            if tool_name == "send_email":
                email_args = arguments
                continue

            elif tool_name in TOOL_ROUTER:
                target_worker = TOOL_ROUTER[tool_name]
                task = {"tool": tool_name, "arguments": arguments}
                try:
                    res = await send_task_via_bus(target_worker.name, task, timeout=60)
                    raw_result = res.get("result", res.get("error")) if res else "未知错误"

                    if "error" in res or "失败" in str(raw_result) or "错误" in str(raw_result):
                        print(f"[Saga] 步骤{step_id}失败，开始补偿...")
                        compensation_msgs = []
                        for comp_step, comp_args, comp_result in reversed(completed_steps):
                            comp_func_name = comp_step.get("tool")
                            if comp_func_name in COMPENSATIONS:
                                try:
                                    comp_msg = COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                                    compensation_msgs.append(comp_msg)
                                    print(f"[Saga] 补偿 {comp_func_name}: {comp_msg}")
                                except Exception as comp_exc:
                                    compensation_msgs.append(f"补偿失败: {comp_exc}")
                        answer = f"任务执行失败（步骤{step_id}），已自动回滚。\n错误: {raw_result}"
                        if compensation_msgs:
                            answer += "\n补偿操作：\n" + "\n".join(f"  - {msg}" for msg in compensation_msgs)
                        memory.append(session_id, query, answer)
                        return output_guard(answer)

                    results[step_id] = raw_result
                    completed_steps.append((step, arguments, raw_result))
                    print(f"[规划引擎] 步骤{step_id}完成: {str(raw_result)[:80]}")

                except asyncio.TimeoutError:
                    print(f"[Saga] 步骤{step_id}超时，开始补偿...")
                    for comp_step, comp_args, comp_result in reversed(completed_steps):
                        comp_func_name = comp_step.get("tool")
                        if comp_func_name in COMPENSATIONS:
                            try:
                                COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                            except Exception:
                                pass
                    answer = f"任务执行超时（步骤{step_id}），已自动回滚。"
                    memory.append(session_id, query, answer)
                    return output_guard(answer)
                except Exception as e:
                    print(f"[Saga] 步骤{step_id}异常，开始补偿: {e}")
                    for comp_step, comp_args, comp_result in reversed(completed_steps):
                        comp_func_name = comp_step.get("tool")
                        if comp_func_name in COMPENSATIONS:
                            try:
                                COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                            except Exception:
                                pass
                    answer = f"任务执行异常（步骤{step_id}），已自动回滚。原因: {e}"
                    memory.append(session_id, query, answer)
                    return output_guard(answer)
            else:
                results[step_id] = f"工具 {tool_name} 未配置"

        if email_args:
            body = email_args.get("body", "")
            for step_id, result_text in results.items():
                body = body.replace(f"{{step_{step_id}_result}}", str(result_text))
            email_args["body"] = body
            pending[session_id] = {"tool_name": "send_email", "arguments": email_args}
            save_pending(pending)
            confirm_msg = (
                f"⚠️ 危险操作确认\n"
                f"工具：send_email\n"
                f"收件人：{email_args.get('to_email')}\n"
                f"主题：{email_args.get('subject')}\n"
                f"内容：{email_args.get('body')}\n\n"
                f"请回复 **“确认”** 以执行，或回复其他内容取消。"
            )
            return confirm_msg
        else:
            raw_info = "\n".join([f"{step['description']}: {str(results[step['id']])[:500]}" for step in plan if step['tool'] != 'send_email'])
            if len(raw_info) > 10000:
                raw_info = raw_info[:10000] + "\n...（内容过长，已截断）"
            summary_prompt = f"用户需求：{query}\n\n以下是执行结果：\n{raw_info}\n\n请根据用户需求，从以上结果中提取或总结出用户想要的信息，用简洁清晰的格式回答。"
            messages.append({"role": "user", "content": summary_prompt})
            try:
                summary_resp = client.chat.completions.create(model="deepseek-chat", messages=messages, temperature=0.3, max_tokens=2000)
                answer = summary_resp.choices[0].message.content
            except Exception as e:
                answer = f"结果整合失败: {e}"
            answer = output_guard(answer)
            if image_output:
                answer = answer + "\n\n" + image_output
            memory.append(session_id, query, answer)
            return answer
    else:
        # ========== 常规单步/多工具调用模式 ==========
        for _ in range(8):
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    tools=TOOLS_METADATA,
                    tool_choice="auto"
                )
            except Exception as e:
                answer = f"模型调用失败: {e}"
                memory.append(session_id, query, answer)
                return output_guard(answer)

            msg = response.choices[0].message
            if msg.tool_calls:
                messages.append(msg)
                for tool_call in msg.tool_calls:
                    func_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    arguments["_tenant"] = memory.get_tenant(session_id)

                    if func_name == "send_email":
                        if tool_call_guard(func_name):
                            pending[session_id] = {"tool_name": func_name, "arguments": arguments}
                            save_pending(pending)
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
                    elif func_name in TOOL_ROUTER:
                        target_worker = TOOL_ROUTER[func_name]
                        task = {"tool": func_name, "arguments": arguments}
                        try:
                            res = await send_task_via_bus(target_worker.name, task, timeout=60)
                            raw_result = res.get("result", res.get("error")) if res else "未知错误"
                        except asyncio.TimeoutError:
                            print(f"[规划引擎] 步骤{step_id}超时，正在重试...")
                            try:
                                res = await send_task_via_bus(target_worker.name, task, timeout=60)
                            except asyncio.TimeoutError:
                                raw_result = "任务执行超时，请稍后重试。"
                        except Exception as e:
                            raw_result = f"任务调用失败: {e}"
                        if func_name == "generate_image" and not str(raw_result).startswith("图像生成"):
                            image_output = raw_result
                            result = "图片已生成，将在最终回答中展示。"
                        else:
                            result = raw_result
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

        answer = output_guard(answer)
        if image_output:
            answer = answer + "\n\n" + image_output
        memory.append(session_id, query, answer)
        return answer

# ========== 规划生成函数 ==========
async def generate_plan(user_query, history, client):
    try:
        prompt = f"""
你是一个任务规划器。根据用户的需求，生成一个 JSON 格式的执行计划。
当前可用的工具及必须遵守的参数：
- query_database: 查询员工数据库（参数必须为 "sql"）
- web_search: 搜索互联网（参数必须为 "query"）
- fetch_webpage: 抓取网页全文（参数必须为 "url"）
- execute_python: 安全执行 Python 代码（参数必须为 "code"）
- get_current_time: 获取当前时间（无参数）
- calculator: 数学计算（参数必须为 "expression"）
- analyze_file: 分析 CSV/Excel 文件（参数必须为 "file_path"）
- send_email: 发送邮件（参数必须为 "to_email", "subject", "body"）
- generate_image: 根据文字描述生成图片（参数必须为 "prompt"）
- add_event: 添加日程（参数必须为 "title", "start_time"）
- list_events: 列出日程（可选参数 "date"）
- delete_event: 删除日程（参数必须为 "event_id"）

计划是一个步骤列表，每个步骤包含：
- id: 步骤唯一编号（从1开始）
- tool: 要调用的工具名称
- arguments: 工具参数字典
- depends_on: 依赖的步骤id列表
- description: 步骤的中文描述

【核心规则】
1. 所有数学计算必须使用 calculator 或 SQL 聚合函数，严禁使用 execute_python 进行算术运算。
2. 如果步骤需要用到前一步的结果，请在 arguments 中使用占位符 {{step_X_result}}。
3. send_email 必须放在最后一个步骤，且需要用户确认。
4. 只返回 JSON 数组，不要有任何额外文字。
5. **严禁将数据库查询结果直接填入 calculator 表达式**，calculator 的参数必须是单行纯数字与运算符，如 "60000+75000+55000+68000"。若要进行求和、求平均等聚合操作，必须使用 query_database 的 SQL 聚合函数（如 SELECT SUM(salary) FROM employees）。
6. 所有工具参数（特别是 calculator 的 expression）不得包含换行符、表格符号或任何非数字非运算符字符。
7. 当用户提到相对日期（如“明天”“后天”），必须将 add_event 或 list_events 的日期参数转换为 YYYY-MM-DD 格式（例如“2026-08-09”），严禁直接使用中文描述。

正确示例（查询所有工资并计算总和，必须使用 SQL 聚合）：
[
  {{{{ "id": 1, "tool": "query_database", "arguments": {{{{ "sql": "SELECT SUM(salary) FROM employees" }}}}, "depends_on": [], "description": "计算工资总和" }}}}
]

用户需求：{user_query}
"""
        messages = history + [{"role": "user", "content": prompt}]
        resp = client.chat.completions.create(model="deepseek-chat", messages=messages, temperature=0)
        plan_text = resp.choices[0].message.content
        json_match = re.search(r'\[.*\]', plan_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        print(f"[规划引擎] 生成计划失败: {e}")
        return None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)