# common/main.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json, asyncio, uuid, traceback, re
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI
import datetime
from common.memory import memory
try:
    from common import rag
except ImportError:
    rag = None

from common.tools import (
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
from common.guardrails import input_guard, tool_call_guard, output_guard
from common.pending_tools import pending, save_pending

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

【日程与时间强制规则】
- 在回答任何与日程、时间、日期相关的问题时，必须严格逐字引用工具返回的 start_time 字段中的年份、月份和日期，严禁自行修改或推断。
- 如果用户问“明天”，你必须先调用 get_current_time 获取当前日期，再基于该日期计算明天，并将计算后的日期作为参数传递给 list_events 或 add_event。计算过程不得影响返回给用户的日期展示。
- 当你调用 list_events 获得结果后，只准直接复述结果中的内容，不准添加“查询 2025-...”等虚构信息。

【重要】
当添加、删除日程后，如果需要确认操作结果，必须调用 list_events 查看最新日程，严禁根据猜测或历史信息回答。
当用户询问实时信息时，请调用 web_search。
当用户要求计算或数据分析时，可调用 execute_python 执行代码。
如果 web_search 返回的结果包含“(实时搜索暂时不可用)”，请在回答中首先说明搜索服务暂时受限，然后根据提供的模拟信息给出参考。
所有工具调用结果会返回给你，你据此生成最终回答。
如果对话历史中出现了以“【上传文件：...】”开头的用户消息，说明用户已上传文件并附带了内容，你必须直接基于这些内容回答用户的问题，不得调用 web_search、query_database 或其他工具去查找外部信息。

【文件处理规则】当用户上传文件后，对话历史中会出现以“【上传文件：...】”开头的消息，该消息包含文件内容。如果用户要求提取文字、分析表格、识别内容等，你必须直接使用该消息中的文件内容回答，严禁调用 ocr_image、recognize_table 或 analyze_file 等工具。仅在对话历史中不存在文件内容时才调用工具。

【时间查询强制规则】
- 每当用户询问当前时间、现在几点、什么时间等类似问题时，你必须立即调用 get_current_time 工具获取最新的北京时间，严禁直接从对话历史或记忆中提取之前的时间。
- 即使上一轮对话刚刚询问过时间，本轮也必须重新调用工具。
- 在回答中必须逐字引用工具返回的结果，不得修改。

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
    
def enhanced_log_plan(session_id, user_query, plan, results, step_times, final_status, total_time, completed_steps=None):
    entry = {
        "timestamp": datetime.datetime.datetime.now().isoformat(),
        "session_id": session_id,
        "tenant": memory.get_tenant(session_id),
        "user_query": user_query,
        "plan": plan,
        "results": {str(k): str(v)[:300] for k, v in results.items()},
        "step_times": step_times,
        "final_status": final_status,
        "total_time": round(total_time, 3),
    }
    if completed_steps is not None:
        entry["completed_steps"] = [{"tool": s[0]["tool"], "description": s[0].get("description"), "result": str(s[2])[:200]} for s in completed_steps]

    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print("[规划日志] 增强日志已写入", flush=True)
    except Exception as e:
        print(f"[规划日志] 写入失败: {e}", flush=True)
        import traceback
        traceback.print_exc()  


def simple_log_tool(session_id, user_query, tool_name, arguments, result):
    """记录非规划模式的工具调用"""
    entry = {
        "timestamp": datetime.datetime.now().isoformat(),   # 注意这里是 datetime.datetime
        "session_id": session_id,
        "tenant": memory.get_tenant(session_id),
        "user_query": user_query,
        "tool": tool_name,
        "arguments": {k: v for k, v in arguments.items() if k != "_tenant"},
        "result": str(result)[:300],
        "mode": "regular"
    }
    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print("[日志] 常规工具调用已记录", flush=True)
    except Exception as e:
        print(f"[日志] 记录失败: {e}", flush=True)


@app.post("/chat")
async def chat(request: ChatRequest):
    # 这里需要传入 query_worker, command_worker, TOOL_ROUTER，将在 app.py 中注入全局变量或通过依赖注入
    # 简便起见，我们使用全局变量 _query_worker, _command_worker, _tool_router，它们在 app.py 中被赋值
    global _query_worker, _command_worker, _tool_router
    answer = await chat_core(request.session_id, request.query, _query_worker, _command_worker, _tool_router)
    return {"answer": answer}

# 全局占位，将由 app.py 设置
_query_worker = None
_command_worker = None
_tool_router = None

def set_workers(query_worker, command_worker, tool_router):
    global _query_worker, _command_worker, _tool_router
    _query_worker = query_worker
    _command_worker = command_worker
    _tool_router = tool_router

# ========== 核心聊天逻辑 ==========
async def chat_core(session_id: str, query: str, query_worker, command_worker, TOOL_ROUTER, image_base64: str = None):
    print(f"[DEBUG] 收到请求: session_id={session_id}, query={query[:50]}...")
    print(f"[DEBUG] 当前 pending keys: {list(pending.keys())}")

    # 0. 输入护栏
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg

    # 惰性启动 Worker
    if not query_worker.is_running:
        asyncio.create_task(query_worker.run_loop())
        query_worker.is_running = True
    if not command_worker.is_running:
        asyncio.create_task(command_worker.run_loop())
        command_worker.is_running = True
    print("Workers ready: QueryWorker, CommandWorker")

    # 1. 确认回复
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
        # ========== 规划模式 ==========
        results = {}
        step_times = {}   # 记录每个步骤的耗时（秒）
        start_total = time.monotonic()  # 总计时开始
        email_args = None
        completed_steps = []

        for step in plan:
            step_id = step["id"]
            step_desc = step.get("description", f"步骤{step_id}")
            tool_name = step["tool"]
            step_start = time.monotonic()            
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
                    res = await target_worker.send_task(task)
                    raw_result = res.get("result", res.get("error")) if res else "未知错误"

                    if "error" in res or "失败" in str(raw_result) or "错误" in str(raw_result):
                        # -------- Saga 补偿回滚（简洁流程提示） --------
                        print(f"[Saga] 步骤{step_id}失败，开始补偿...")
                        # 构建流程步骤状态
                        steps_summary = []
                        for comp_step, comp_args, comp_result in completed_steps:
                            comp_desc = comp_step.get("description", f"步骤{comp_step['id']}")
                            steps_summary.append(f"✅ {comp_desc}")
                        steps_summary.append(f"❌ {step_desc}（遇到问题）")

                        # 执行补偿
                        compensation_msgs = []
                        for comp_step, comp_args, comp_result in reversed(completed_steps):
                            comp_func_name = comp_step.get("tool")
                            if comp_func_name in COMPENSATIONS:
                                try:
                                    comp_msg = COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                                    # 提取补偿的友好描述（去掉原始JSON）
                                    if "已删除" in comp_msg or "已取消" in comp_msg:
                                        compensation_msgs.append(f"🔄 {comp_step.get('description', '未知步骤')} 已回滚")
                                    else:
                                        compensation_msgs.append(f"🔄 {comp_msg}")
                                    print(f"[Saga] 补偿 {comp_func_name}: {comp_msg}")
                                except Exception as comp_exc:
                                    compensation_msgs.append(f"🔄 回滚失败: {comp_exc}")
                                    print(f"[Saga] 补偿失败: {comp_exc}")

                        # 构建简洁回答
                        answer = "任务执行情况：\n" + "\n".join(steps_summary)
                        if compensation_msgs:
                            answer += "\n\n" + "\n".join(compensation_msgs)
                        else:
                            answer += "\n\n没有需要回滚的操作。"
                        memory.append(session_id, query, answer)
                        total_time = round(time.monotonic() - start_total, 3)
                        enhanced_log_plan(session_id, query, plan, results, step_times, "failed_with_compensation", total_time, completed_steps)                        
                        return output_guard(answer)

                    # 成功
                    results[step_id] = str(raw_result)
                    step_times[step_id] = round(time.monotonic() - step_start, 3)                    
                    completed_steps.append((step, arguments, raw_result))
                    print(f"[规划引擎] 步骤{step_id}完成: {str(raw_result)[:80]}")

                except asyncio.TimeoutError:
                    print(f"[Saga] 步骤{step_id}超时，开始补偿...")
                    # 类似构建步骤状态和补偿
                    steps_summary = []
                    for comp_step, comp_args, comp_result in completed_steps:
                        comp_desc = comp_step.get("description", f"步骤{comp_step['id']}")
                        steps_summary.append(f"✅ {comp_desc}")
                    steps_summary.append(f"⏱️ {step_desc}（超时）")

                    compensation_msgs = []
                    for comp_step, comp_args, comp_result in reversed(completed_steps):
                        comp_func_name = comp_step.get("tool")
                        if comp_func_name in COMPENSATIONS:
                            try:
                                COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                                compensation_msgs.append(f"🔄 {comp_step.get('description', '未知步骤')} 已回滚")
                            except Exception:
                                pass
                    answer = "任务执行情况：\n" + "\n".join(steps_summary)
                    if compensation_msgs:
                        answer += "\n\n" + "\n".join(compensation_msgs)
                    else:
                        answer += "\n\n没有需要回滚的操作。"
                    memory.append(session_id, query, answer)
                    total_time = round(time.monotonic() - start_total, 3)
                    enhanced_log_plan(session_id, query, plan, results, step_times, "failed_with_compensation", total_time, completed_steps)                    
                    return output_guard(answer)

                except Exception as e:
                    print(f"[Saga] 步骤{step_id}异常，开始补偿: {e}")
                    steps_summary = []
                    for comp_step, comp_args, comp_result in completed_steps:
                        comp_desc = comp_step.get("description", f"步骤{comp_step['id']}")
                        steps_summary.append(f"✅ {comp_desc}")
                    steps_summary.append(f"❌ {step_desc}（系统异常）")

                    compensation_msgs = []
                    for comp_step, comp_args, comp_result in reversed(completed_steps):
                        comp_func_name = comp_step.get("tool")
                        if comp_func_name in COMPENSATIONS:
                            try:
                                COMPENSATIONS[comp_func_name](**comp_args, result=comp_result)
                                compensation_msgs.append(f"🔄 {comp_step.get('description', '未知步骤')} 已回滚")
                            except Exception:
                                pass
                    answer = "任务执行情况：\n" + "\n".join(steps_summary)
                    if compensation_msgs:
                        answer += "\n\n" + "\n".join(compensation_msgs)
                    else:
                        answer += "\n\n没有需要回滚的操作。"
                    memory.append(session_id, query, answer)
                    total_time = round(time.monotonic() - start_total, 3)
                    enhanced_log_plan(session_id, query, plan, results, step_times, "failed_with_compensation", total_time, completed_steps)                    
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
            to = email_args.get('to_email', '未知')
            subject = email_args.get('subject', '无主题')
            body = email_args.get('body', '无内容')
            body_display = body.replace('\\n', '\n')
            confirm_msg = (
                f"### ⚠️ 危险操作确认\n"
                f"**收件人**：{to}\n\n"
                f"**主题**：{subject}\n\n"
                f"**内容预览**：\n{body_display[:500]}\n\n"
                f"> 请回复 **“确认”** 以执行，或回复其他内容取消。"
            )
            total_time = round(time.monotonic() - start_total, 3)
            enhanced_log_plan(session_id, query, plan, results, step_times, "pending_email_confirmation", total_time, completed_steps) 
            simple_log_tool(session_id, query, func_name, arguments, "pending_confirmation")            
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
            total_time = round(time.monotonic() - start_total, 3)
            enhanced_log_plan(session_id, query, plan, results, step_times, "success", total_time, completed_steps)            
            return answer
    else:
        # ========== 常规模式 ==========
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
                total_time = round(time.monotonic() - start_total, 3)
                enhanced_log_plan(session_id, query, plan, results, step_times, "failed_with_compensation", total_time, completed_steps)                
                return output_guard(answer)

            msg = response.choices[0].message
            if msg.tool_calls:
                messages.append(msg)
                for tool_call in msg.tool_calls:
                    func_name = tool_call.function.name
                    arguments = json.loads(tool_call.function.arguments)
                    arguments["_tenant"] = memory.get_tenant(session_id)
                     # 如果用户已上传文件，禁止调用文件分析工具
                    if func_name in ("ocr_image", "recognize_table", "analyze_file") and any("【上传文件：" in msg.get("content", "") for msg in messages if msg["role"] == "user"):
                        result = "文件内容已在对话历史中，请直接基于该内容回答，不要调用工具。"
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result
                        })
                        simple_log_tool(session_id, query, func_name, arguments, result)                        
                        continue                   

                    if func_name in ("ocr_image", "speech_to_text", "recognize_table"):
                        required_param = "image_path" if func_name != "speech_to_text" else "audio_file_path"
                        if required_param not in arguments:
                            result = f"错误：工具 {func_name} 缺少 {required_param} 参数。请先上传文件。"
                            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
                            simple_log_tool(session_id, query, func_name, arguments, result)                            
                            continue

                    if func_name == "send_email":
                        if tool_call_guard(func_name):
                            pending[session_id] = {"tool_name": func_name, "arguments": arguments}
                            save_pending(pending)
                            to = arguments.get("to_email", "未知")
                            subject = arguments.get("subject", "无主题")
                            body = arguments.get("body", "无内容")
                            body_display = body.replace('\\n', '\n')
                            confirm_msg = (
                                f"### ⚠️ 危险操作确认\n"
                                f"**收件人**：{to}\n\n"
                                f"**主题**：{subject}\n\n"
                                f"**内容预览**：\n{body_display[:500]}\n\n"
                                f"> 请回复 **“确认”** 以执行，或回复其他内容取消。"
                            )
                            simple_log_tool(session_id, query, func_name, arguments, "pending_confirmation")
                            return confirm_msg
                        try:
                            result = AVAILABLE_TOOLS[func_name](**arguments)
                        except Exception as e:
                            result = f"工具执行错误: {e}"
                    elif func_name in TOOL_ROUTER:
                        target_worker = TOOL_ROUTER[func_name]
                        task = {"tool": func_name, "arguments": arguments}
                        try:
                            res = await target_worker.send_task(task)
                            raw_result = res.get("result", res.get("error")) if res else "未知错误"
                        except Exception as e:
                            raw_result = f"工具调用失败: {e}"
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
                simple_log_tool(session_id, query, func_name, arguments, result)                    
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
- add_event: 添加日程（参数必须为 "title" 和 "start_time"，start_time 必须是绝对日期时间，格式为 "YYYY-MM-DD HH:MM"，例如 "2026-08-10 09:00"，严禁使用相对描述或需要计算的占位符）
- list_events: 列出日程（可选参数 "date"）
- delete_event: 删除日程（参数必须为 "event_id"）
- ocr_image: 识别图片文字（参数必须为 "image_path"）
- speech_to_text: 语音转文字（参数必须为 "audio_file_path"）
- recognize_table: 识别图片中的表格（参数必须为 "image_path"）

计划是一个步骤列表，每个步骤包含：
- id: 步骤唯一编号（从1开始）
- tool: 要调用的工具名称
- arguments: 工具参数字典
- depends_on: 依赖的步骤id列表
- description: 步骤的中文描述

【核心规则】
1. 所有数学计算必须使用 calculator 或 SQL 聚合函数，严禁使用 execute_python 进行算术运算。
2. 如果步骤需要用到前一步的结果，请在 arguments 中使用占位符 {step_X_result}。
3. send_email 必须放在最后一个步骤，且需要用户确认。
4. 只返回 JSON 数组，不要有任何额外文字。
5. **文件已上传时禁止调用文件工具**：如果对话历史中包含以“【上传文件：...】”开头的消息，说明文件内容已提供，**绝对禁止**使用 ocr_image、recognize_table、analyze_file 等需要文件路径的工具。用户要求提取文字、分析表格时，直接让模型基于历史内容回答，不要生成任何工具步骤。
6. 禁止使用任何需要计算的占位符（如 {step_1_result_date_plus_1}），必须直接计算并写入绝对日期时间。
7. 严禁将数据库查询结果直接填入 calculator 表达式，calculator 的参数必须是单行纯数字与运算符，如 "60000+75000+55000+68000"。
8. 所有工具参数不得包含换行符或表格符号。
9. 当用户提到相对日期（如“明天”），必须将 add_event 或 list_events 的日期参数转换为 YYYY-MM-DD 格式。
10. 添加日程时，start_time 必须精确到分钟，格式为 "YYYY-MM-DD HH:MM"，不得添加秒或时区信息。

正确示例（查询所有工资并计算总和）：
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
        
