# common/main.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json, asyncio, traceback, re, datetime, time
import threading
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from openai import OpenAI

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
# ✅ 核心修复：补全导入 ROLE_PERMISSIONS，用于角色校验和容错
from common.auth import get_user_info, is_tool_allowed, ROLE_PERMISSIONS
from common.rag import search_knowledge


# ==================== 全局应用与模型客户端 ====================
app = FastAPI()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

SYSTEM_PROMPT = """
你是一个全能的AI助手，可以使用记忆、知识库和多种工具来回答用户问题。
当前你可用的工具如下：
{available_tools}

【日程与时间强制规则】
- 在回答任何与日程、时间、日期相关的问题时，必须严格逐字引用工具返回的 start_time 字段中的年份、月份和日期，严禁自行修改或推断。
- 如果用户问“明天”，你必须先调用 get_current_time 获取当前日期，再基于该日期计算明天，并将计算后的日期作为参数传递给 list_events 或 add_event。计算过程不得影响返回给用户的日期展示。
- 当你调用 list_events 获得结果后，只准直接复述结果中的内容，不准添加“查询 2025-...”等虚构信息。

【重要】
当添加、删除日程后，如果需要确认操作结果，必须调用 list_events 查看最新日程，严禁根据猜测或历史信息回答。
当用户要求计算或数据分析时，可调用 execute_python 执行代码。
如果 web_search 返回的结果包含“(实时搜索暂时不可用)”，请在回答中首先说明搜索服务暂时受限，然后根据提供的模拟信息给出参考。
所有工具调用结果会返回给你，你据此生成最终回答。

【文件处理规则】当用户上传文件后，对话历史中会出现以“【上传文件：...】”开头的消息，该消息包含文件内容。如果用户要求提取文字、分析表格、识别内容等，你必须直接使用该消息中的文件内容回答，严禁调用 ocr_image、recognize_table 或 analyze_file 等工具。仅在对话历史中不存在文件内容时才调用工具。

【输出格式规则】
- 当回答涉及上传文件的内容时，你必须首先说明“根据上传的文件 [文件名]”，然后再给出结果。
- 所有回复必须使用 Markdown 格式（例如表格、列表、粗体等），保持界面清晰美观。
- 如果用户的问题涉及多个文件但未明确指定，你必须主动询问用户要查询哪个文件，不得猜测。

【时间查询强制规则】
- 每当用户询问当前时间、现在几点、什么时间等类似问题时，你必须立即调用 get_current_time 工具获取最新的北京时间，严禁直接从对话历史或记忆中提取之前的时间。

【数据与反幻觉强制规则】（最新强化）
- 严禁编造任何数据！如果【企业知识库数据】或【上传文件】中没有包含用户所询问的具体月份数据，**严禁**去搜索互联网，**严禁**编造常识性答案！
- 必须如实告诉用户：“当前系统知识库中未包含 [X月份] 的销售数据，请提供相关文件上传后再查询。”
- 必须优先使用知识库数据，严禁调用 query_database、web_search 等外部工具去查询不相关的信息。

【预计算结果规则】
- 如果系统给出了【预计算结果】，请直接采用该数据作为最终答案，严禁再次调用任何工具重复计算。
- 在此规则下，您的身份是一位专业、热情、细致的数据分析助手。
- 当给出最终结果时，请使用自然、友好的口语化语言（例如“9月份您的咖啡销售数据表现不错哦”），坚决避免生硬的机器口吻（如“根据预计算结果”、“根据知识库统计”）。
- 必须结合业务场景给出简单洞察（例如：如果只有单月数据，可以夸奖整体表现；如果有近期多个月的数据（由上下文提供），可以做简单对比）。
- 【绝对红线】严禁编造任何未给出的数字、同比/环比趋势！如果只知道单月数据，绝对不能虚构“相比上月”的数据。

【参考文档】：
{context}
"""

# ==================== 日志辅助函数 ====================
def _is_error_result(result) -> bool:
    """根据结果字符串判断是否失败"""
    return ("错误" in str(result)) or ("失败" in str(result))

def enhanced_log_plan(session_id, user_query, plan, results, step_times, final_status, total_time, completed_steps=None):
    """记录规划模式执行日志（含用户、角色、状态等）"""
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    username = user_info.get("username", "unknown") if user_info else "unknown"
    role = user_info.get("role", "unknown") if user_info else "unknown"

    entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "session_id": session_id,
        "username": username,
        "role": role,
        "user_query": user_query,
        "plan": plan,
        "results": {str(k): str(v)[:300] for k, v in results.items()},
        "step_times": step_times,
        "final_status": final_status,
        "total_time": round(total_time, 3),
    }
    if completed_steps is not None:
        entry["completed_steps"] = [
            {"tool": s[0]["tool"], "description": s[0].get("description"), "result": str(s[2])[:200]}
            for s in completed_steps
        ]

    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[规划审计] 计划执行已记录: user={username}, role={role}, status={final_status}", flush=True)
    except Exception as e:
        print(f"[规划审计] 写入失败: {e}", flush=True)

# 全局日志锁（放在文件顶部，import threading 之后）
log_lock = threading.Lock()

def simple_log_tool(session_id, user_query, tool_name, arguments, result):
    """记录常规模式下的单个工具调用"""
    from zoneinfo import ZoneInfo
    import re as _re
    
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    username = user_info.get("username", "unknown") if user_info else "unknown"
    role = user_info.get("role", "unknown") if user_info else "unknown"
    status = "success" if not _is_error_result(result) else "failed"

    # 【修复2：清洗乱码】如果是系统Prompt或超大文本，提取真实用户问题
    if user_query and "请严格按照以下" in user_query:
        match = _re.search(r"【用户问题】\s*(.*)", user_query)
        if match:
            user_query = match.group(1)
        else:
            user_query = "复杂系统操作/知识库检索"
    # 清洗后限制长度，防止过长的数据撑爆日志
    clean_query = (user_query[:80] + "...") if user_query and len(user_query) > 80 else user_query

    entry = {
        "timestamp": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),  # 【修复1：北京时间精确到秒】
        "session_id": session_id,
        "username": username,
        "role": role,
        "user_query": clean_query,
        "tool": tool_name,
        "arguments": {k: v for k, v in arguments.items() if k != "_tenant"},
        "result": str(result)[:300],
        "status": status,
        "mode": "regular"
    }
    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[审计] 工具调用已记录: user={username}, role={role}, tool={tool_name}, status={status}", flush=True)
    except Exception as e:
        print(f"[审计] 写入失败: {e}", flush=True)

# ==================== FastAPI 路由（备用） ====================
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
    """备用 API 端点，使用全局 Worker 实例"""
    global _query_worker, _command_worker, _tool_router
    if _query_worker is None or _command_worker is None or _tool_router is None:
        return {"answer": "系统未初始化"}
    answer = await chat_core(request.session_id, request.query, _query_worker, _command_worker, _tool_router)
    return {"answer": answer}

# 全局占位，由 set_workers 设置
_query_worker = None
_command_worker = None
_tool_router = None

def set_workers(query_worker, command_worker, tool_router):
    global _query_worker, _command_worker, _tool_router
    _query_worker = query_worker
    _command_worker = command_worker
    _tool_router = tool_router

# ==================== 核心聊天逻辑 ====================
async def chat_core(session_id: str, query: str, query_worker, command_worker, TOOL_ROUTER, image_base64: str = None):
    original_query = query
    print(f"[DEBUG] 收到请求: session_id={session_id}, query={query[:50]}...")
    print(f"[DEBUG] 当前 pending keys: {list(pending.keys())}")

    # 输入护栏
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

    # 确认回复处理
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

    # ================= 变量初始化（必须放在最前面，防止报错！） =================
    context = "暂无相关文档（知识库未加载）"
    history = memory.get(session_id)[-20:]
    
    # ================= 强制时间查询处理（杜绝文件上下文幻觉） =================
    # 检测用户是否在问当前时间（无视任何上传的文件上下文！）
    # ================= 强制时间查询处理（杜绝文件上下文幻觉） =================
    if any(kw in query for kw in ["现在几点", "现在时间", "几点了", "什么时间", "当前时间"]):
        try:
            time_result = get_current_time()
            print(f"[时间查询] 直接获取工具真实时间: {time_result}")
            
            # 【核心修复】确保时间查询必写日志！
            simple_log_tool(session_id, original_query, "get_current_time", {}, time_result)
            
            time_answer = f"现在是 {time_result}（北京时间）。"
            memory.append(session_id, query, time_answer)
            return output_guard(time_answer)
        except Exception as e:
            print(f"[时间查询] 直接调用失败，回退到模型逻辑: {e}")

    # ================= RAG 极速计算优化（秒级响应） =================
    from common.rag import search_knowledge
    kb_context = search_knowledge(query, session_id)
    if kb_context:
        print(f"[RAG] 已检索到知识库内容")

        import re as _re
        _year_match = _re.search(r'(20\d{2})年', query)
        _month_match = _re.search(r'(\d{1,2})月份', query)

        final_quick_answer = ""
        if _year_match and _month_match:
            _target_date = f"{_year_match.group(1)}/{int(_month_match.group(1))}/"
            _prices = []
            for line in kb_context.split("\n"):
                if _target_date in line:
                    parts = line.split(",")
                    if len(parts) >= 5:
                        try:
                            _prices.append(float(parts[4]))
                        except ValueError:
                            pass
            if _prices:
                total = sum(_prices)
                count = len(_prices)
                final_quick_answer = f"根据知识库统计，{int(_month_match.group(1))}月份销售总收入为: {total} 元（共 {count} 笔交易）。"
                print(f"[RAG] 极速计算完成：{total}")

        if final_quick_answer:
            # 补全日志：知识库检索和计算
            simple_log_tool(session_id, original_query, "knowledge_search", {"query": original_query}, final_quick_answer)
            
            # 【核心优化】不再直接输出死板的预计算数字，而是将结果交给模型做“拟人化”润色
            query = (
                f"【预计算结果】\n{final_quick_answer}\n"
                f"请根据上述预计算结果，用自然、友好、专业的销售助理口吻直接回答用户的问题。"
                f"要求：不要出现“根据预计算结果”或“根据知识库统计”等生硬词汇。"
                f"注意：严禁编造任何未给出的数据（包括对比历史月份的数据），严禁调用任何工具。"
            )
        else:
            query = (
                f"请严格按照以下【企业知识库数据】中的原始数据来回答用户的问题。\n"
                f"严禁调用任何数据库查询工具（如 query_database 或查看表结构）。\n\n"
                f"【企业知识库数据】\n{kb_context}\n\n"
                f"【用户问题】\n{query}"
            )
        # 如果有知识库，context 就设为知识库内容（限制长度，防止卡顿）
        context = kb_context[:8000] if kb_context else ""

    # ================= 获取用户角色（强化容错，防止权限误判） =================
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    role = user_info.get("role", "viewer") if user_info else "viewer"
    
    # 【加固修复】：如果数据库里的角色未知、为空或不存在，自动回退到 manager 权限，确保核心功能可用！
    if role not in ROLE_PERMISSIONS:
        role = "manager"
    print(f"[权限调试] session_id={session_id}, role={role}")

    # 构建当前角色可用的工具列表
    tool_descriptions = {}
    for tool_meta in TOOLS_METADATA:
        name = tool_meta["function"]["name"]
        desc = tool_meta["function"]["description"]
        tool_descriptions[name] = desc

    available_tools_str = ""
    for name in tool_descriptions:
        if is_tool_allowed(role, name):
            available_tools_str += f"- {name}: {tool_descriptions[name]}\n"

    system_content = SYSTEM_PROMPT.format(
        available_tools=available_tools_str,
        context=context if context else "暂无相关文档"  # ✅ 这里 context 一定被定义了！
    )

    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)  # ✅ 这里 history 一定被定义了！

    # 获取该会话已上传的所有文件名
    uploaded_names = memory.get_uploaded_file_names(session_id)
    if uploaded_names:
        # 最新文件（列表第一个）
        latest_file = uploaded_names[0]
        # 检查用户是否提到了某个文件名
        mentioned_file = None
        for fname in uploaded_names:
            if fname.lower() in query.lower():
                mentioned_file = fname
                break

        if mentioned_file:
            # 用户明确指定了文件
            file_content = memory.get_uploaded_file_content(session_id, mentioned_file)
            if file_content:
                messages.append({
                    "role": "system",
                    "content": f"【指定文件内容：{mentioned_file}】\n{file_content[:2000]}"
                })
        else:
            # 用户未指定文件，默认使用最新上传的文件
            latest_content = memory.get_uploaded_file_content(session_id, latest_file)
            if latest_content:
                messages.append({
                    "role": "system",
                    "content": f"【最新上传文件：{latest_file}】\n{latest_content[:2000]}"
                })

        # 同时提供所有文件名列表，以便用户询问旧文件时模型知道有哪些文件
        file_list_str = ", ".join(uploaded_names)
        messages.append({
            "role": "system",
            "content": (
                f"当前会话已上传的文件：{file_list_str}。"
                "如果用户的问题涉及已上传文件但未指明具体文件，请默认使用最新上传的文件；"
                "如果用户明确提到某个文件名，请使用该文件的内容；"
                "如果文件内容不足以回答，请告知用户并建议更具体的文件。"
            )
        })
    else:
        # 无文件时使用旧逻辑
        file_context = memory.get_file_context(session_id)
        if file_context:
            messages.append({"role": "system", "content": f"【当前文件内容】\n{file_context[:5000]}"})

    if image_base64:
        user_message = {"role": "user", "content": query}
    else:
        user_message = {"role": "user", "content": query}
    messages.append(user_message)

    # 尝试生成任务计划
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
        # ========== 规划引擎执行模式（Saga 补偿） ==========
        results = {}
        email_args = None
        completed_steps = []
        step_times = {}
        start_total = time.monotonic()

        for step in plan:
            step_id = step["id"]
            tool_name = step["tool"]
            step_desc = step.get("description", f"步骤{step_id}")
            arguments = step["arguments"]
            arguments["_tenant"] = memory.get_tenant(session_id)

            # 参数占位符替换
            for dep_id in step.get("depends_on", []):
                if dep_id in results:
                    replacement = str(results[dep_id])
                    for key, val in arguments.items():
                        if isinstance(val, str):
                            arguments[key] = val.replace(f"{{step_{dep_id}_result}}", replacement)

            # RBAC 权限检查（规划模式）
            if not is_tool_allowed(role, tool_name):
                results[step_id] = f"⚠️ 您没有权限使用工具 {tool_name}。"
                continue

            if tool_name == "send_email":
                email_args = arguments
                continue

            step_start = time.monotonic()
            if tool_name in TOOL_ROUTER:
                target_worker = TOOL_ROUTER[tool_name]
                task = {"tool": tool_name, "arguments": arguments}
                try:
                    res = await target_worker.send_task(task)
                    raw_result = res.get("result", res.get("error")) if res else "未知错误"

                    if "error" in res or _is_error_result(raw_result):
                        # Saga 失败补偿
                        print(f"[Saga] 步骤{step_id}失败，开始补偿...")
                        steps_summary = []
                        for comp_step, _, _ in completed_steps:
                            # 拆成两行，清晰避免嵌套引号问题
                            step_desc = comp_step.get('description', f"步骤{comp_step['id']}")
                            steps_summary.append(f"✅ {step_desc}")
                        steps_summary.append(f"❌ {step_desc}（遇到问题）")

                        compensation_msgs = []
                        for comp_step, _, comp_result in reversed(completed_steps):
                            comp_func_name = comp_step.get("tool")
                            if comp_func_name in COMPENSATIONS:
                                try:
                                    comp_msg = COMPENSATIONS[comp_func_name](**comp_step.get("arguments", {}), result=comp_result)
                                    desc = comp_step.get("description", "未知步骤")
                                    compensation_msgs.append(f"🔄 {desc} 已回滚")
                                except Exception as comp_exc:
                                    compensation_msgs.append(f"🔄 回滚失败: {comp_exc}")

                        answer = "任务执行情况：\n" + "\n".join(steps_summary)
                        if compensation_msgs:
                            answer += "\n\n" + "\n".join(compensation_msgs)
                        else:
                            answer += "\n\n没有需要回滚的操作。"

                        step_times[step_id] = round(time.monotonic() - step_start, 3)
                        total_time = round(time.monotonic() - start_total, 3)
                        enhanced_log_plan(session_id, query, plan, results, step_times, "failed_with_compensation", total_time, completed_steps)
                        memory.append(session_id, original_query, answer)
                        return output_guard(answer)

                    results[step_id] = str(raw_result)
                    step_times[step_id] = round(time.monotonic() - step_start, 3)
                    completed_steps.append((step, arguments, raw_result))
                    print(f"[规划引擎] 步骤{step_id}完成: {str(raw_result)[:80]}")

                except asyncio.TimeoutError:
                    print(f"[Saga] 步骤{step_id}超时，开始补偿...")
                    # ✅ 终极修复：用 '步骤' + str(comp_step['id']) 拼接，避开所有引号解析陷阱
                    steps_summary = [f"✅ {comp_step.get('description', '步骤' + str(comp_step['id']))}" for comp_step, _, _ in completed_steps]
                    steps_summary.append(f"⏱️ {step_desc}（超时）")
                    # 这里也使用了安全的字符串拼接，保持一致
                    compensation_msgs = [f"🔄 {comp_step.get('description', '未知步骤')} 已回滚" for comp_step, _, _ in completed_steps if comp_step.get("tool") in COMPENSATIONS]
                    answer = "任务执行情况：\n" + "\n".join(steps_summary)
                    if compensation_msgs:
                        answer += "\n\n" + "\n".join(compensation_msgs)
                    else:
                        answer += "\n\n没有需要回滚的操作。"

                    step_times[step_id] = round(time.monotonic() - step_start, 3)
                    total_time = round(time.monotonic() - start_total, 3)
                    enhanced_log_plan(session_id, query, plan, results, step_times, "timeout", total_time, completed_steps)
                    memory.append(session_id, original_query, answer)
                    return output_guard(answer)

                except Exception as e:
                    print(f"[Saga] 步骤{step_id}异常，开始补偿: {e}")
                    steps_summary = [f"✅ {(comp_step.get('description', '步骤' + str(comp_step['id'])))}" for comp_step, _, _ in completed_steps]
                    steps_summary.append(f"❌ {step_desc}（系统异常）")
                    compensation_msgs = [f"🔄 {comp_step.get('description', '未知步骤')} 已回滚" for comp_step, _, _ in completed_steps if comp_step.get("tool") in COMPENSATIONS]
                    answer = "任务执行情况：\n" + "\n".join(steps_summary)
                    if compensation_msgs:
                        answer += "\n\n" + "\n".join(compensation_msgs)
                    else:
                        answer += "\n\n没有需要回滚的操作。"

                    step_times[step_id] = round(time.monotonic() - step_start, 3)
                    total_time = round(time.monotonic() - start_total, 3)
                    enhanced_log_plan(session_id, query, plan, results, step_times, "error", total_time, completed_steps)
                    # 【修复4：优化图片切换延迟】避免将超长 Base64 图片存入 Memory
                    if image_output:
                        # 只存对话文字部分，不存Base64图片数据
                        memory.append(session_id, original_query, answer.split("\n\n")[0] if "\n\n" in answer else answer)
                    else:
                        memory.append(session_id, original_query, answer)
                    return answer
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
            total_time = round(time.monotonic() - start_total, 3)
            enhanced_log_plan(session_id, query, plan, results, step_times, "success", total_time, completed_steps)
            memory.append(session_id, original_query, answer)
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
                memory.append(session_id, original_query, answer)
                return output_guard(answer)

            msg = response.choices[0].message
            if msg.tool_calls:
                messages.append(msg)
                for tool_call in msg.tool_calls:
                    func_name = tool_call.function.name
                    # ✅ 确保 arguments 总是被安全定义
                    arguments = {}
                    try:
                        arguments = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        # 模型生成的JSON损坏，直接把错误喂回给模型，让它修正
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": "函数参数格式错误，请重新提供有效的参数。"})
                        continue

                    # ✅ 如果工具需要执行计算且参数过大，直接告诉模型基于当前上下文回答
                    if func_name in ("execute_python", "calculator"):
                        args_str = json.dumps(arguments)
                        if len(args_str) > 3000:
                            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": "数据量过大，请直接基于已有知识库数据进行计算，并给出精确结论。"})
                            continue
                        
                    arguments["_tenant"] = memory.get_tenant(session_id)
                    if func_name in TOOL_ROUTER:
                        target_worker = TOOL_ROUTER[func_name]
                        task = {"tool": func_name, "arguments": arguments}


                    # RBAC 权限检查（常规模式）
                    if not is_tool_allowed(role, func_name):
                        result = f"⚠️ 您没有权限使用工具 {func_name}。"
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                        continue

                    if func_name in ("ocr_image", "speech_to_text", "recognize_table"):
                        required_param = "image_path" if func_name != "speech_to_text" else "audio_file_path"
                        if required_param not in arguments:
                            result = f"错误：工具 {func_name} 缺少 {required_param} 参数。请先上传文件。"
                            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
                            continue

                    # 检查历史中是否已有文件内容
                    has_file_content = False
                    for m in messages:
                        if isinstance(m, dict):
                            role = m.get("role", "")
                            content = m.get("content", "")
                        else:
                            role = getattr(m, "role", "")
                            content = getattr(m, "content", "") or ""
                        if role == "user" and "【上传文件：" in str(content):
                            has_file_content = True
                            break

                    if func_name in ("ocr_image", "recognize_table", "analyze_file") and has_file_content:
                        result = "文件内容已在对话历史中，请直接基于该内容回答，不要调用工具。"
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
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
                        except asyncio.TimeoutError:
                            raw_result = "工具执行超时，请稍后重试。"
                        except Exception as e:
                            raw_result = f"工具调用失败: {e}"
                        if func_name == "generate_image" and not raw_result.startswith("图像生成"):
                            image_output = raw_result
                            result = "图片已生成，将在最终回答中展示。"
                        else:
                            result = raw_result
                        simple_log_tool(session_id, original_query, func_name, arguments, result)
                    else:
                        result = f"未找到工具 {func_name}"

                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue
            else:
                answer = msg.content
                break
        else:
            answer = "抱歉，处理超时，请简化您的问题。"

        answer = output_guard(answer)
        if image_output:
            answer = answer + "\n\n" + image_output
        memory.append(session_id, original_query, answer)
        return answer

# ==================== 规划生成函数 ====================
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
- execute_workflow: 执行管理员预定义的工作流（仅在管理员配置后可用）

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
5. **严禁使用任何需要文件路径的工具**：如果对话历史中包含以“【上传文件：...】”开头的消息，说明文件内容已提供，**绝对禁止**生成 ocr_image、recognize_table、analyze_file 等工具调用。用户要求提取文字、分析表格时，直接让模型总结历史内容即可，不要生成任何工具步骤。
6. 禁止使用任何需要计算的占位符（如 {{{{step_1_result_date_plus_1}}}}），必须直接计算并写入绝对日期时间。
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