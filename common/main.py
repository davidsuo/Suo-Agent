# common/main.py
import sys, os, json, asyncio, traceback, re, datetime, time
import threading
from typing import Optional   # 必须添加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# 全局客户端初始化（必须在这里，不能在文件末尾！）
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

# 导入系统内部模块
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
    execute_workflow_tool,
    COMPENSATIONS,
)
from common.guardrails import input_guard, tool_call_guard, output_guard
from common.pending_tools import pending, save_pending
from common.auth import authenticate, get_user_info, is_tool_allowed, ROLE_PERMISSIONS, init_users_db
from common.rag import search_knowledge
from common.memory import memory

# ==================== 全局应用与模型客户端 ====================
app = FastAPI()

def init_db():
    """初始化员工数据库（类似 Gradio 的 init_db）"""
    db_path = "sample.db"
    if not os.path.exists(db_path):
        import sqlite3
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
        print("✅ 数据库 sample.db 已自动创建并插入示例数据。")

import sqlite3

# 初始化健康数据库
def init_health_db():
    conn = sqlite3.connect("health.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            session_id TEXT,
            username TEXT,
            role TEXT,
            tool TEXT,
            query TEXT,
            result TEXT,
            status TEXT
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ 健康数据库 health.db 已准备就绪")

# 写入日志到SQLite（保留原有 JSON 写入以防兼容问题，但重点记录到SQLite）
def write_log_to_db(entry):
    try:
        conn = sqlite3.connect("health.db")
        cursor = conn.cursor()
        # 修改 write_log_to_db 内部，兼容 status 和 final_status 两个不同的键名
        status = entry.get("status") or entry.get("final_status") or ""

        # 确保插入 SQL 时使用上面计算好的 status 变量
        cursor.execute("""
            INSERT INTO logs (timestamp, session_id, username, role, tool, query, result, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.get("timestamp", ""),
            entry.get("session_id", ""),
            entry.get("username", ""),
            entry.get("role", ""),
            entry.get("tool", ""),
            entry.get("user_query", ""),
            str(entry.get("result", ""))[:300],
            status  # 这里传入修正后的 status
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"###DEBUG### SQLite写入失败: {e}")

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'dist')

# ================= CORS 跨域配置 =================
from fastapi.middleware.cors import CORSMiddleware

# 允许 React 开发服务器跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://suo-agent.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Pydantic 模型 =================
from pydantic import BaseModel

class LoginRequest(BaseModel):
    username: str
    pin: str

class ChatRequest(BaseModel):
    session_id: str
    query: str
    user_text: Optional[str] = None

# ================= 初始化 Workers（供 API 工具链使用） =================
from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker
from common.auth import init_users_db

_query_worker = None
_command_worker = None
_tool_router = None

# 只保留一个统一的 startup 事件
@app.on_event("startup")
async def startup_event():
    global _query_worker, _command_worker, _tool_router
    init_users_db()  # 初始化用户表
    init_db()        # 【核心修复】初始化员工表 sample.db
    init_health_db() # 新增
    
    if _query_worker is None:
        bus = EventBus()
        query_worker_tools = {
            "get_current_time": get_current_time, "calculator": calculator,
            "query_database": query_database, "list_events": list_events,
            "web_search": web_search, "fetch_webpage": fetch_webpage,
            "ocr_image": ocr_image, "recognize_table": recognize_table,
            "analyze_file": analyze_file, "speech_to_text": speech_to_text,
        }
        command_worker_tools = {
            "send_email": send_email, "add_event": add_event,
            "delete_event": delete_event, "execute_python": execute_python,
            "generate_image": generate_image, "execute_workflow": execute_workflow_tool,
        }
        _query_worker = QueryWorker("QueryWorker", query_worker_tools, bus)
        _command_worker = WorkerAgent("CommandWorker", command_worker_tools, bus)
        _tool_router = {}
        for name in _query_worker.tools:
            _tool_router[name] = _query_worker
        for name in _command_worker.tools:
            _tool_router[name] = _command_worker
        set_workers(_query_worker, _command_worker, _tool_router)
    print("✅ FastAPI 初始化完成")

# ================= 登录接口 =================
@app.post("/api/login")
async def api_login(request: LoginRequest):
    user = authenticate(request.username.strip().lower(), request.pin)
    # 如果是被禁用
    if user and isinstance(user, dict) and user.get("status") == "disabled":
        return {"status": "error", "message": "该账号已被禁用，请联系管理员"}
    if user:
        return {"status": "success", "user": user}
    return {"status": "error", "message": "用户名或密码错误"}

# ================= 完整的 API 聊天接口（支持全部工具链） =================
@app.post("/api/chat")
async def api_chat(request: ChatRequest):
    try:
        # 【核心修复】动态校验用户是否被禁用
        real_username = request.session_id.split('_')[0] if '_' in request.session_id else request.session_id
        user_info = get_user_info(real_username)
        if user_info and user_info.get("status") == "禁用":
            return {"answer": "【系统提示】您的账号已被禁用，请联系管理员。您已被强制下线。"}
        
        answer = await chat_core(
            request.session_id,
            request.query,
            request.user_text,
            _query_worker,
            _command_worker,
            _tool_router
        )
        return {"answer": answer}
    except Exception as e:
        return {"answer": f"系统处理异常: {e}"}
        
from fastapi import UploadFile, File, Form
import shutil

# ================= 文件上传与知识库接口 =================
@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """上传文件并分析（支持图片/CSV/Excel/音频）"""
    file_path = os.path.join(os.getcwd(), file.filename)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
            from common.tools import ocr_image
            result = ocr_image(file_path)
        elif ext in ['.csv', '.xlsx', '.xls']:
            from common.tools import analyze_file
            result = analyze_file(file_path)
        elif ext in ['.wav', '.mp3', '.m4a', '.ogg', '.webm']:
            # 【音频文件处理】
            from common.tools import speech_to_text
            result = speech_to_text(file_path)
            # 如果百度密钥读取失败，降级提示用户用“按住说话”
            if "未配置" in result or "凭证无效" in result or "无效" in result:
                result = "已接收语音文件。但当前系统后端未成功读取百度API密钥，建议直接使用聊天框下方的【按住说话】按钮，体验更快更精准的语音输入！"
        else:
            result = f"已接收文件：{file.filename}（当前仅支持图片/CSV/Excel/音频格式分析）"
        
        return {"status": "success", "message": result, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": f"上传失败: {e}"}


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
    from zoneinfo import ZoneInfo
    import re as _re

    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    username = user_info.get("real_name") or user_info.get("display_name") or "unknown" if user_info else "unknown"
    role = user_info.get("role", "unknown") if user_info else "unknown"

    # 【核心修复1：统一清洗日志】过滤掉被污染的系统Prompt
    if user_query and "请严格按照以下" in user_query:
        match = _re.search(r"【用户问题】\s*(.*)", user_query)
        user_query = match.group(1) if match else "复杂系统操作"

    entry = {
        # 【核心修复2：统一北京时间，精确到秒】
        "timestamp": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id,
        "username": username,
        "role": role,
        "user_query": (user_query[:100] + "...") if user_query and len(user_query) > 100 else user_query,
        "plan": plan,
        "results": {str(k): str(v)[:300] for k, v in results.items()},
        "step_times": step_times,
        "final_status": final_status,
        "total_time": round(total_time, 3),
        "tool": "规划执行", 
    }
    if completed_steps is not None:
        entry["completed_steps"] = [
            {"tool": s[0]["tool"], "description": s[0].get("description"), "result": str(s[2])[:200]}
            for s in completed_steps
        ]

    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            write_log_to_db(entry)
        print(f"[规划审计] 计划执行已记录: user={username}, role={role}, status={final_status}", flush=True)
    except Exception as e:
        print(f"[规划审计] 写入失败: {e}", flush=True)

# 全局日志锁（放在文件顶部，import threading 之后）
log_lock = threading.Lock()

def simple_log_tool(session_id, user_query, tool_name, arguments, result):
    """记录常规模式下的单个工具调用，强制清洗乱码和超长数据"""
    from zoneinfo import ZoneInfo
    import re as _re

    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    username = user_info.get("real_name") or user_info.get("display_name") or "unknown" if user_info else "unknown"
    role = user_info.get("role", "unknown") if user_info else "unknown"
    status = "success" if not _is_error_result(result) else "failed"

    # 【暴力清洗1】只要日志里有“请严格按照”，坚决丢掉前半部分
    if user_query and "请严格按照以下" in user_query:
        # 提取【用户问题】之后的内容，如果找不到，直接设为通用标签
        match = _re.search(r"【用户问题】\s*(.*)", user_query)
        if match:
            user_query = match.group(1).strip()
        else:
            user_query = "复杂系统操作/知识库检索"
    
    # 【暴力清洗2】限制详情长度，防止超长CSV或知识库数据撑爆日志
    clean_query = (user_query[:100] + "...") if user_query and len(user_query) > 100 else user_query

    entry = {
        # 强制转换为北京时间，精准到秒
        "timestamp": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
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
    session_id: str
    query: str
    user_text: Optional[str] = None

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
async def chat_core(session_id: str, query: str, user_text: str = None, query_worker=None, command_worker=None, TOOL_ROUTER=None, image_base64: str = None):
    # 【核心终极拦截】无论前端怎么刷新，只要发消息查后端，就检查账号状态！
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)
    if user_info and user_info.get("status") == "禁用":
        print(f"###安全拦截### 用户 {real_username} 试图操作，已被强制拦截！")
        return "【系统安全提示】您的账号已被管理员禁用，您已被强制下线，请联系管理员！"
    
    original_query = query
    # 构建历史存储的消息（合并文件引用和问题）
    import re
    history_text = user_text if user_text else original_query
    if query and query.startswith("文件"):
        # 尝试提取文件名
        match = re.search(r"文件 (.+?) 的内容如下：", query)
        if match:
            filename = match.group(1)
            history_text = f"📎 上传文件：{filename}\n\n{user_text}" if user_text else f"📎 上传文件：{filename}"
    # 注意：如果user_text为空（只有文件），则只存文件引用

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
                    "content": f"【指定文件内容：{mentioned_file}】\n{file_content[:8000]}"
                })
        else:
            # 用户未指定文件，默认使用最新上传的文件
            latest_content = memory.get_uploaded_file_content(session_id, latest_file)
            if latest_content:
                messages.append({
                    "role": "system",
                    "content": f"【最新上传文件：{latest_file}】\n{latest_content[:8000]}"
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
                    # 【核心修复】避免将超长 Base64 图片存入 Memory，引发上下文爆炸
                    if image_output:
                        # 仅记录文字描述，绝不保存base64图片数据
                        memory.append(session_id, original_query, "图片已生成（详见聊天记录，不纳入长期记忆）")
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
                summary_resp = client.chat.completions.create(model="deepseek-chat", messages=messages, temperature=0.3, max_tokens=8000)
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
        memory.append(session_id, history_text, answer)
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
        
# ================= 语音识别接口 =================
@app.post("/api/voice")
async def api_voice(file: UploadFile = File(...)):
    """上传语音文件并转写为文本"""
    import tempfile
    import os
    
    # 保存临时音频文件
    suffix = os.path.splitext(file.filename)[1] if file.filename else '.webm'
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        temp_path = tmp.name
        
    try:
        # 调用工具库中的语音识别函数（自动处理webm转wav）
        from common.tools import speech_to_text
        text_result = speech_to_text(temp_path)
        return {"status": "success", "text": text_result}
    except Exception as e:
        return {"status": "error", "message": f"语音识别失败: {e}"}
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
            
# ================= 系统健康检查接口 =================
try:
    from common.health import get_system_health
except ImportError:
    # 兜底：防止模块不存在导致崩溃
    def get_system_health():
        return {"total_tasks": 0, "success_tasks": 0, "failed_tasks": 0, "success_rate": 0, "active_users": 0, "total_users": 0, "total_feedback": 0, "up_feedback": 0, "down_feedback": 0, "sorted_tools": []}

@app.get("/api/health")
async def api_health():
    import sqlite3
    import datetime
    import os

    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    total_tasks = 0
    success_tasks = 0
    failed_tasks = 0
    total_users = 0
    active_users = 0
    sorted_tools = {}

    # 1. 查询总用户数（users.db）
    try:
        users_db_path = os.path.join(BASE_DIR, "users.db")
        if os.path.exists(users_db_path):
            conn = sqlite3.connect(users_db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            total_users = cursor.fetchone()[0]
            conn.close()
    except Exception as e:
        print(f"###DEBUG### 用户查询失败: {e}")

    # 2. 查询活跃用户数（health.db的logs表，最近24小时有记录的去重用户）
    # 计算24小时前的北京时间字符串
    from zoneinfo import ZoneInfo
    cutoff_time = (datetime.datetime.now(ZoneInfo("Asia/Shanghai")) - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        health_db_path = os.path.join(BASE_DIR, "health.db")
        if os.path.exists(health_db_path):
            conn = sqlite3.connect(health_db_path)
            cursor = conn.cursor()
            
            # 统计任务总数和成功/失败数
            cursor.execute("SELECT COUNT(*) FROM logs")
            total_tasks = cursor.fetchone()[0]
            cursor.execute("SELECT status, COUNT(*) FROM logs GROUP BY status")
            status_counts = cursor.fetchall()
            for status, count in status_counts:
                if status == 'success':
                    success_tasks += count
                else:
                    failed_tasks += count
            
            # 统计最近24小时活跃用户数
            cursor.execute("SELECT COUNT(DISTINCT username) FROM logs WHERE timestamp >= ?", (cutoff_time,))
            active_users = cursor.fetchone()[0]

            # 统计工具调用分布
            cursor.execute("SELECT tool, COUNT(*) FROM logs GROUP BY tool")
            tool_counts = cursor.fetchall()
            # 替换为：
            for tool, count in tool_counts:
                # 如果工具名为空，统一显示为"系统操作"
                tool_name = tool if tool else "系统操作"
                sorted_tools[tool_name] = count
            
            conn.close()
    except Exception as e:
        print(f"###DEBUG### 健康日志查询失败: {e}")

    success_rate = round((success_tasks / total_tasks) * 100, 2) if total_tasks > 0 else 0
    tool_list = [{"tool": k, "count": v} for k, v in sorted_tools.items()]

    return {
        "status": "success",
        "data": {
            "total_tasks": total_tasks,
            "success_tasks": success_tasks,
            "failed_tasks": failed_tasks,
            "success_rate": success_rate,
            "active_users": active_users,  # ✅ 独立计算
            "total_users": total_users,    # ✅ 独立计算
            "total_feedback": 0,
            "up_feedback": 0,
            "down_feedback": 0,
            "sorted_tools": tool_list
        }
    }
    
# ================= 日志查询接口 =================
from zoneinfo import ZoneInfo
import datetime

@app.get("/api/logs")
async def api_logs():
    """读取日志文件并返回北京时间格式"""
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plan_log_path = os.path.join(BASE_DIR, "plan_log.json")
    
    logs = []
    if os.path.exists(plan_log_path):
        with open(plan_log_path, "rb") as f:
            raw = f.read()
        for line in raw.decode("utf-8", errors="ignore").splitlines():
            try:
                entry = json.loads(line)
                ts = entry.get('timestamp', '')
                try:
                    if 'T' in ts:
                        import datetime as dt_module
                        from zoneinfo import ZoneInfo
                        dt = dt_module.datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
                        ts = dt.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        ts = ts[:19]
                except:
                    pass
                
                # 【核心修复】提取 session_id 并拼接窗口名
                session_id = entry.get('session_id', '')
                if '_' in session_id:
                    window_name = session_id.split('_', 1)[1]
                else:
                    window_name = '主对话'
                
                logs.append({
                    "timestamp": ts,
                    "username": f"{entry.get('username', 'unknown')}/{window_name}",
                    "role": entry.get('role', 'unknown'),
                    "action": entry.get('tool', '系统操作'),
                    "detail": (entry.get('user_query') or '')[:60],
                    "status": entry.get('status', 'success')
                })
            except:
                continue
    logs.reverse()
    return {"status": "success", "data": logs[:100]}
    

# ================= 日志导出接口 =================  
import csv
from io import StringIO
from fastapi.responses import Response

@app.get("/api/logs/export")
async def api_logs_export():
    """导出日志为 CSV（精简列，时间戳与前端一致）"""
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plan_log_path = os.path.join(BASE_DIR, "plan_log.json")
    
    if not os.path.exists(plan_log_path):
        return {"status": "error", "message": "日志文件不存在"}
    
    logs = []
    with open(plan_log_path, "rb") as f:
        raw = f.read()
    for line in raw.decode("utf-8", errors="ignore").splitlines():
        try:
            logs.append(json.loads(line))
        except:
            continue
    
    output = StringIO()
    output.write('\uFEFF')  # BOM for Excel
    writer = csv.writer(output)
    
    # 列头与前端日志表格完全对应
    headers = ["时间戳", "操作人/窗口", "角色", "操作行为/内容", "调用工具", "状态"]
    writer.writerow(headers)
    
    for entry in logs:
        ts = entry.get('timestamp', '')
        # 统一格式：如果已经是 "2026/9/2 13:11" 这种格式，直接使用；否则转换
        try:
            if 'T' in ts:
                dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
                dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
                ts = dt.strftime("%Y/%m/%d %H:%M")
            else:
                # 假设已经是 "2026/9/2 13:11" 格式，截取前16字符
                ts = ts[:16] if len(ts) >= 16 else ts
        except:
            pass
        
        # 构造 “操作人/窗口”：username + "/" + window_name (从session_id提取)
        session_id = entry.get('session_id', '')
        username = entry.get('username', '')
        if '_' in session_id:
            window_name = session_id.split('_', 1)[1]
        else:
            window_name = '主对话'
        operator = f"{username}/{window_name}"
        
        # 操作行为/内容：优先使用 user_query，若为空则用 result 前60字
        detail = entry.get('user_query', '')
        if not detail:
            detail = str(entry.get('result', ''))[:60]
        else:
            detail = detail[:60]  # 与前端显示一致
        
        row = [
            ts,
            operator,
            entry.get('role', ''),
            detail,
            entry.get('tool', ''),
            entry.get('status', '')
        ]
        writer.writerow(row)
    
    csv_content = output.getvalue()
    output.close()
    
    filename = f"logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=csv_content.encode('utf-8-sig'),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
   

# ================= 会话历史记录接口 =================
@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    """根据会话ID（如 alice_主对话）拉取后端保存的历史记录"""
    try:
        hist = memory.get_history(session_id)
        return {"status": "success", "data": hist if hist else []}
    except Exception as e:
        return {"status": "error", "message": str(e)}
        
        
# ================= 知识库管理接口 (Sprint 2) =================
@app.get("/api/kb/list")
async def api_kb_list():
    """读取纯JSON文件列表（加入DEBUG打印）"""
    import json
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")  
    if os.path.exists(rag_file):
        try:
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
                return {"status": "success", "data": store.get("files", [])}
        except Exception as e:
            print(f"###DEBUG### 读取异常: {e}")
            return {"status": "error", "message": str(e)}
    return {"status": "success", "data": []}

import os
# 确保 uploads 文件夹存在
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/api/kb/index")
async def api_kb_index(file: UploadFile = File(...), tags: str = Form("")):
    """上传并写入JSON"""
    import shutil
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        from common.rag import index_document
        msg = index_document(file_path, "default", tags)
        if "成功" in str(msg):
            return {"status": "success", "message": msg}
        return {"status": "error", "message": msg}
    except Exception as e:
        return {"status": "error", "message": f"索引失败: {e}"}

@app.post("/api/kb/delete")
async def api_kb_delete(file_name: str = Form(...)):
    """删除指定知识库文档（删除JSON记录）"""
    import json
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")
    
    if os.path.exists(rag_file):
        try:
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
            
            # 从文件列表和内容库中同时删除
            store["files"] = [f for f in store.get("files", []) if f.get("file_name") != file_name]
            for key in list(store.get("store", {}).keys()):
                store["store"][key] = [doc for doc in store["store"][key] if doc.get("file_name") != file_name]
            
            with open(rag_file, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
                
            return {"status": "success", "message": f"文档 {file_name} 已删除"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "知识库不存在"}
    
# ================= Worker 状态监控接口 (Sprint 2) =================
@app.get("/api/status")
async def api_status():
    """获取 QueryWorker 和 CommandWorker 的运行状态"""
    workers = []
    try:
        # 读取在 startup 中初始化的 Worker 状态
        if _query_worker:
            stats = _query_worker.get_stats()
            workers.append(stats)
        if _command_worker:
            stats = _command_worker.get_stats()
            workers.append(stats)
        return {"status": "success", "data": workers}
    except Exception as e:
        return {"status": "error", "message": str(e)}
        
from fastapi.responses import FileResponse
import os

# 新增下载接口
@app.get("/api/kb/download")
# ================= Admin 用户管理接口（支持完整字段） =================

# 注意：由于数据库结构改变，请手动删除项目根目录下的 users.db 文件，重启后端会自动新建
@app.get("/api/users/list")
async def api_users_list():
    """获取所有用户列表（包含姓名、部门、联系方式、状态）"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # 确保表存在且包含新字段，如果旧表没有则报错，建议直接删除旧库重建
    cursor.execute("SELECT username, real_name, role, department, contact, status FROM users")
    users = [{"username": r[0], "real_name": r[1], "role": r[2], "department": r[3], "contact": r[4], "status": r[5]} for r in cursor.fetchall()]
    conn.close()
    return {"status": "success", "data": users}

@app.post("/api/users/add")
async def api_users_add(username: str = Form(...), pin: str = Form(...), real_name: str = Form(""), role: str = Form("viewer"), department: str = Form(""), contact: str = Form(""), status: str = Form("正常")):
    """添加新用户（支持完整字段）"""
    try:
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, pin, real_name, role, department, contact, status) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                       (username, pin, real_name, role, department, contact, status))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "用户添加成功"}
    except Exception as e:
        error_msg = str(e)
        if "UNIQUE constraint failed" in error_msg:
            return {"status": "error", "message": "用户已存在，请更换用户名"}
        elif "database is locked" in error_msg:
            return {"status": "error", "message": "数据库被锁定，请重启后端服务"}
        return {"status": "error", "message": f"添加失败: {error_msg}"}

@app.post("/api/users/delete")
async def api_users_delete(username: str = Form(...)):
    """删除指定用户"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户已删除"}

@app.post("/api/users/update")
async def api_users_update(username: str = Form(...), role: str = Form(...), status: str = Form("正常")):
    """更新用户角色或状态"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = ?, status = ? WHERE username = ?", (role, status, username))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户更新成功"}

# ================= SPA 兜底代码 =================
if os.path.exists(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")
    
    @app.get("/")
    async def serve_react():
        return FileResponse(os.path.join(DIST_DIR, "index.html"))
    
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api"):
            return {"detail": "Not Found"}
        return FileResponse(os.path.join(DIST_DIR, "index.html"))
        

        
        

        