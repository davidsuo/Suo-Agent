import os, json
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
from memory import memory
import re
from pending_tools import pending, save_pending
from tools import generate_image
import time
import datetime
from agents import WorkerAgent, QueryWorker

try:
    import rag
except ImportError:
    rag = None

from tools import (
    TOOLS_METADATA,
    AVAILABLE_TOOLS,
    get_current_time,
    calculator,
    query_database,
    web_search,
    execute_python,
    speech_to_text,
    analyze_file,
    fetch_webpage,
    generate_image,
    ocr_image,
    add_event, 
    list_events, 
    delete_event,
    recognize_table,
    send_email,  
    COMPENSATIONS, 
)

from guardrails import input_guard, tool_call_guard, output_guard
from pending_tools import pending
import asyncio


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
query_worker = QueryWorker("QueryWorker", query_worker_tools)

# ========== 命令类 Worker ==========
command_worker_tools = {
    "send_email": send_email,
    "add_event": add_event,
    "delete_event": delete_event,
    "execute_python": execute_python,
    "generate_image": generate_image,
}
command_worker = WorkerAgent("CommandWorker", command_worker_tools)   # 不需要缓存的命令 Worker

# ========== 工具路由表 ==========
TOOL_ROUTER = {}
for name in query_worker.tools:
    TOOL_ROUTER[name] = query_worker
for name in command_worker.tools:
    TOOL_ROUTER[name] = command_worker

# 监控用列表（如果需要）
ALL_WORKERS = [query_worker, command_worker]

def get_workers_status():
    """返回所有 Worker 的状态列表"""
    return [w.get_stats() for w in ALL_WORKERS]

app = FastAPI()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """
你是一个全能的AI助手。当调用工具获得结果后，必须严格基于工具返回的信息回答问题，严禁编造或声称无法获取。
可用工具：
- get_current_time: 获取当前时间
- calculator: 数学计算
- query_database: 查询员工数据库
- send_email: 发送邮件
- web_search: 搜索互联网获取最新信息
- execute_python: 执行Python代码进行计算或数据处理
- analyze_file: 分析CSV/Excel文件
- generate_image: 根据文字描述生成图片（建议使用英文提示词）
- ocr_image: 识别图片中的文字（参数必须为 "image_file_path"）
- add_event: 添加日程
- list_events: 列出日程
- delete_event: 删除日程
- recognize_table: 识别图片中的表格，返回 CSV

【强制规则】当工具返回时间、计算结果等信息时，你必须直接使用这些数据生成简洁回答，严禁说“无法获取”、“抱歉”、“工具未返回”等话语。如果工具返回了有效数据，就原样呈现。
当用户询问实时信息（如新闻、股价、天气）时，请调用 web_search。
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
        <h2>AI 智能体（记忆 + 知识库 + 工具 + 语音 + 分析文件）</h2>
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

def log_plan(user_query, plan, results):
    """将规划执行记录追加到 plan_log.json"""
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "user_query": user_query,
        "plan": plan,
        "results": results
    }
    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
        print("[日志] 规划记录已写入 plan_log.json")
    except Exception as e:
        print(f"[日志] 写入规划日志失败: {e}")

def call_deepseek_with_retry(messages, tools=None, temperature=0, max_retries=3, max_tokens=None):
    """带自动重试的 DeepSeek 调用"""
    for attempt in range(max_retries):
        try:
            kwargs = {
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if max_tokens:
                kwargs["max_tokens"] = max_tokens

            return client.chat.completions.create(**kwargs)
        except Exception as e:
            print(f"[DeepSeek] 调用失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避
            else:
                raise

    
async def generate_plan(user_query, history, client):
    try:
        prompt = f"""
你是一个任务规划器。根据用户的需求，生成一个 JSON 格式的执行计划。
当前可用的工具及**必须遵守的参数**：
- query_database: 查询员工数据库（参数必须为 "sql"，例如 {{"sql": "SELECT * FROM employees"}}）
- web_search: 搜索互联网（参数必须为 "query" 和可选的 "max_results"）
- fetch_webpage: 抓取指定 URL 的网页全文（参数必须为 "url"）
- execute_python: 安全执行 Python 代码（参数必须为 "code"，**仅用于独立的逻辑判断或格式化输出，不得用于数学计算**）
- get_current_time: 获取当前时间（无参数）
- calculator: 数学计算（参数必须为 "expression"，表达式只能包含数字和运算符，如 "60000+75000+55000+68000"）
- analyze_file: 分析 CSV/Excel 文件（参数必须为 "file_path"）
- send_email: 发送邮件（参数必须为 "to_email", "subject", "body"）
- generate_image: 根据文字描述生成图片（参数必须为 "prompt", 可选 "negative_prompt"）


计划是一个步骤列表，每个步骤包含：
- id: 步骤唯一编号（从1开始）
- tool: 要调用的工具名称（必须从上面的列表中选择）
- arguments: 工具参数字典，键名必须与上述规定完全一致
- depends_on: 依赖的步骤id列表（如果没有则为空列表）
- description: 步骤的中文描述

【核心规则】
1. 所有数学计算（求和、平均等）必须使用 calculator 工具或 query_database 的 SQL 聚合函数（如 SUM）。**严禁使用 execute_python 进行任何算术运算**。
2. 如果步骤需要用到前一步的结果，请在 arguments 中使用占位符 {{step_X_result}}（X 是步骤id），但 calculator 的表达式必须直接写死具体数字，不能包含占位符。如果需要从查询结果中提取数字，请生成单独的步骤，将数字直接写入 calculator 的 expression。
3. send_email 必须放在最后一个步骤，且需要用户确认。
4. 只返回 JSON 数组，不要有任何额外文字。

【核心规则补充】
- 调用 add_event 时，必须提供 title 和 start_time 参数，格式为 "YYYY-MM-DD HH:MM"。
- 调用 send_email 时，必须提供 to_email、subject、body。
- 调用 query_database 时，必须提供 sql 参数。
- 所有参数必须符合工具要求的类型和格式，不得遗漏必填字段。

用户需求：{user_query}

正确示例（查询所有工资并计算总和，通过 SQL 聚合完成）：
[
  {{{{ "id": 1, "tool": "query_database", "arguments": {{{{ "sql": "SELECT SUM(salary) FROM employees" }}}}, "depends_on": [], "description": "计算工资总和" }}}}
]

如果必须用 calculator，示例（手动列出数字）：
[
  {{{{ "id": 1, "tool": "calculator", "arguments": {{{{ "expression": "60000+75000+55000+68000" }}}}, "depends_on": [], "description": "计算工资总和" }}}}
]
"""
        messages = history + [{"role": "user", "content": prompt}]
        resp = call_deepseek_with_retry(messages, temperature=0)
        plan_text = resp.choices[0].message.content
        json_match = re.search(r'\[.*\]', plan_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return None
    except Exception as e:
        print(f"[规划引擎] 生成计划失败: {e}")
        return None

# ========== 核心聊天逻辑 ==========
async def chat_core(session_id: str, query: str, image_base64: str = None):
    image_output = None   # 用于存储图像生成的完整 base64 结果，最后附加到回答
    print(f"[DEBUG] 收到请求: session_id={session_id}, query={query[:50]}...")
    print(f"[DEBUG] 当前 pending keys: {list(pending.keys())}")  

    # 0. 输入护栏
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg

    # 惰性启动查询和命令 Workers
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

    # 5. 根据是否有计划选择执行模式
    if plan:
        # ========== 规划引擎执行模式（Saga 补偿版） ==========
        results = {}
        email_args = None
        completed_steps = []  # 记录已成功执行的步骤信息 (step, arguments)

        for step in plan:
            step_id = step["id"]
            tool_name = step["tool"]
            arguments = step["arguments"]

            # 处理参数中的占位符替换（{step_X_result}）
            for dep_id in step.get("depends_on", []):
                if dep_id in results:
                    replacement = str(results[dep_id])
                    for key, val in arguments.items():
                        if isinstance(val, str):
                            arguments[key] = val.replace(f"{{step_{dep_id}_result}}", replacement)

            if tool_name == "send_email":
                # 邮件步骤先记录，最后统一处理（需要用户确认）
                email_args = arguments
                continue

            elif tool_name in TOOL_ROUTER:
                task = {"tool": tool_name, "arguments": arguments}
                try:
                    res = await TOOL_ROUTER[tool_name].send_task(task)
                    raw_result = res.get("result", res.get("error")) if res else "未知错误"
                    
                    # 判断是否执行失败
                    if "error" in res or "失败" in str(raw_result) or "错误" in str(raw_result):
                        # -------- Saga 补偿回滚 --------
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
                                    print(f"[Saga] 补偿失败: {comp_exc}")
                        # 构建详细的回滚消息
                        answer = f"任务执行失败（步骤{step_id}），已自动回滚。\n错误: {raw_result}"
                        if compensation_msgs:
                            answer += "\n补偿操作：\n" + "\n".join(f"  - {msg}" for msg in compensation_msgs)
                        memory.append(session_id, query, answer)
                        return output_guard(answer)

                    # 执行成功，记录结果
                    results[step_id] = raw_result
                    completed_steps.append((step, arguments, raw_result))  # 保存步骤及参数，用于可能的补偿
                    print(f"[规划引擎] 步骤{step_id}完成: {str(raw_result)[:80]}")

                except Exception as e:
                    # 网络异常等也触发补偿
                    print(f"[Saga] 步骤{step_id}异常，开始补偿: {e}")
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
                                print(f"[Saga] 补偿失败: {comp_exc}")
                    answer = f"任务执行异常（步骤{step_id}），已自动回滚。原因: {e}"
                    if compensation_msgs:
                        answer += "\n补偿操作：\n" + "\n".join(f"  - {msg}" for msg in compensation_msgs)
                    memory.append(session_id, query, answer)
                    return output_guard(answer)
            else:
                results[step_id] = f"工具 {tool_name} 未配置"

        # ========== 步骤执行完毕，处理邮件确认或结果整合 ==========
        if email_args:
            # 邮件需要二次确认，生成确认提示
            body = email_args.get("body", "")
            for step_id, result_text in results.items():
                body = body.replace(f"{{step_{step_id}_result}}", str(result_text))
            email_args["body"] = body

            pending[session_id] = {
                "tool_name": "send_email",
                "arguments": email_args
            }

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
            # 无邮件步骤，整合结果并进行智能后处理
            raw_info = "\n".join([f"{step['description']}: {str(results[step['id']])[:500]}" for step in plan if step['tool'] != 'send_email'])
            if len(raw_info) > 10000:
                raw_info = raw_info[:10000] + "\n...（内容过长，已截断）"

            summary_prompt = f"用户需求：{query}\n\n以下是执行结果：\n{raw_info}\n\n请根据用户需求，从以上结果中提取或总结出用户想要的信息，用简洁清晰的格式回答。如果结果中包含大量无关内容，请忽略它们，只输出相关部分。"
            messages.append({"role": "user", "content": summary_prompt})
            try:
                summary_resp = call_deepseek_with_retry(messages, temperature=0.3, max_tokens=2000)
                answer = summary_resp.choices[0].message.content
            except Exception as e:
                answer = f"结果整合失败: {e}"

            answer = output_guard(answer)
            if image_output:
                answer = answer + "\n\n" + image_output

            # 记录规划日志
            log_plan(query, plan, results)

            memory.append(session_id, query, answer)
            return answer

    else:
        # ========== 常规单步/多工具调用模式 ==========
        for _ in range(8):
            try:
                response = call_deepseek_with_retry(messages, tools=TOOLS_METADATA, temperature=0)
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
                    elif func_name in TOOL_ROUTER:
                        target_worker = TOOL_ROUTER[func_name]
                        task = {"tool": func_name, "arguments": arguments}
                        res = await target_worker.send_task(task)
                        if not isinstance(res, dict):
                            res = {"error": "Worker 返回无效结果"}
                        raw_result = res.get("result", res.get("error"))
                        if raw_result is None or (isinstance(raw_result, str) and raw_result.strip() == ""):
                            raw_result = "工具未返回有效数据"

                        # 图像生成特殊处理：保存完整结果，发送占位符
                        if func_name == "generate_image" and not str(raw_result).startswith("图像生成"):
                            image_output = raw_result
                            result = "图片已生成，将在最终回答中展示。"
                        else:
                            result = raw_result

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result
                        })
            else:
                print(f"[DEBUG] 最终消息数量: {len(messages)}")
                # 打印最后一条工具消息（如果有）
                if messages and messages[-1]["role"] == "tool":
                    print(f"[DEBUG] 最后一条工具消息: {messages[-1]['content'][:100]}")
           
                answer = msg.content
                # 智能替换：如果模型生成歉意回复，但工具成功返回了结果，则强制使用工具结果
                if any(keyword in answer for keyword in ["无法获取", "暂时无法", "没有返回", "抱歉"]):
                    # 查找最后一条工具消息的内容
                    last_tool_result = None
                    for msg_item in reversed(messages):
                        if msg_item.get("role") == "tool":
                            last_tool_result = msg_item.get("content")
                            break
                    if last_tool_result and not any(err in last_tool_result for err in ["失败", "错误", "未知错误"]):
                        answer = last_tool_result
                        print(f"[智能替换] 模型歉意回复被替换为工具结果: {answer[:100]}...")
                break                
        else:
            answer = "抱歉，处理超时，请简化您的问题。"

        answer = output_guard(answer)
        if image_output:
            answer = answer + "\n\n" + image_output 
        memory.append(session_id, query, answer)            
        return answer


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)