# common/main.py
import sys, os, json, asyncio, re, datetime, time
import threading
from typing import Optional
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import shutil
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

import sqlite3
from zoneinfo import ZoneInfo

# 全局客户端初始化
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
from common.memory import memory

# ==================== 全局应用与模型客户端 ====================
app = FastAPI()
DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'dist')
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ==================== 临时会话文件缓存 ====================
# 【设计原则】聊天框上传的文件是"临时文件"，与知识库文档严格隔离。
# 用途：单次会话内 LLM 分析使用，不污染企业知识库。
import uuid as _uuid_mod
TEMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "temp")
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)

# session_id -> temp_file_path
_session_temp_files: dict = {}

# ================= CORS 跨域配置 =================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://suo-agent.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= Pydantic 模型 =================
class LoginRequest(BaseModel):
    username: str
    pin: str

class ChatRequest(BaseModel):
    session_id: str
    query: str
    user_text: Optional[str] = None
    temp_file_path: Optional[str] = None

# ================= 数据库初始化与辅助函数 =================
def init_db():
    db_path = "sample.db"
    if not os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY, name TEXT, position TEXT, salary INTEGER)''')
        sample_data = [(1, "张三", "工程师", 60000), (2, "李四", "产品经理", 75000), (3, "王五", "设计师", 55000), (4, "赵六", "数据分析师", 68000)]
        cursor.executemany("INSERT OR REPLACE INTO employees VALUES (?,?,?,?)", sample_data)
        conn.commit()
        conn.close()

def init_health_db():
    conn = sqlite3.connect("health.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, session_id TEXT, username TEXT, role TEXT,
        tool TEXT, query TEXT, result TEXT, status TEXT)''')
    conn.commit()
    conn.close()

def write_log_to_db(entry):
    try:
        conn = sqlite3.connect("health.db")
        cursor = conn.cursor()
        status = entry.get("status") or entry.get("final_status") or ""
        cursor.execute("""INSERT INTO logs (timestamp, session_id, username, role, tool, query, result, status)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (entry.get("timestamp", ""), entry.get("session_id", ""), entry.get("username", ""), entry.get("role", ""),
                        entry.get("tool", ""), entry.get("user_query", ""), str(entry.get("result", ""))[:300], status))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"###DEBUG### SQLite写入失败: {e}")

# ================= 初始化 Workers =================
from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker

_query_worker = None
_command_worker = None
_tool_router = None

def set_workers(query_worker, command_worker, tool_router):
    global _query_worker, _command_worker, _tool_router
    _query_worker = query_worker
    _command_worker = command_worker
    _tool_router = tool_router

@app.on_event("startup")
async def startup_event():
    global _query_worker, _command_worker, _tool_router
    init_users_db()
    init_db()
    init_health_db()
    
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
        for name in _query_worker.tools: _tool_router[name] = _query_worker
        for name in _command_worker.tools: _tool_router[name] = _command_worker
        set_workers(_query_worker, _command_worker, _tool_router)
    print("✅ FastAPI 初始化完成")

# ================= 纯语义 Agent 极简系统提示 =================
# 移除任何“规划”、“步骤依赖”等词汇，完全依赖模型自主选择工具
SYSTEM_PROMPT = """
你是一个企业级AI智能助手。请根据用户意图直接调用可用的工具函数。无需制定复杂计划，直接一步到位选择最合适的一个或多个工具解决用户问题。如果知识库和工具都无法解决，请坦诚告知用户。
"""

# ================= 日志辅助函数 =================
def _is_error_result(result) -> bool:
    return ("错误" in str(result)) or ("失败" in str(result))

def simple_log_tool(session_id, user_query, tool_name, arguments, result):
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username) if real_username else None
    username = user_info.get("username", "unknown") if user_info else "unknown"
    role = user_info.get("role", "unknown") if user_info else "unknown"
    status = "success" if not _is_error_result(result) else "failed"
    clean_query = (user_query[:100] + "...") if user_query and len(user_query) > 100 else user_query
    entry = {
        "timestamp": datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        "session_id": session_id, "username": username, "role": role,
        "user_query": clean_query, "tool": tool_name,
        "arguments": {k: v for k, v in arguments.items() if k != "_tenant"},
        "result": str(result)[:300], "status": status, "mode": "semantic_agent"
    }
    try:
        with open("plan_log.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        write_log_to_db(entry)
    except Exception as e:
        print(f"[审计] 写入失败: {e}", flush=True)

log_lock = threading.Lock()

# ================= 核心聊天逻辑 (纯语义 Agent 架构，无规划引擎) =================
def _extract_ids_from_sources(sources: list) -> list:
    """
    从 RAG 返回的 sources 元数据中提取文档 ID。
    独立函数，不作为 chat_core 的嵌套函数。
    """
    if not sources:
        return []
    ids = []
    seen = set()
    for src in sources:
        doc_id = src.get("doc_id")
        if doc_id and doc_id not in seen:
            ids.append(doc_id)
            seen.add(doc_id)
            if len(ids) >= 5:
                break
    return ids

async def chat_core(session_id: str, query: str, user_text: str = None,
                    query_worker=None, command_worker=None, TOOL_ROUTER=None,
                    image_base64: str = None, temp_file_path: str = None):
    """
    纯语义 Agent 架构的对话核心。

    【设计原则】
    1. RAG 提供"语义上下文"，LLM 理解后自主决策
    2. 临时文件只传路径，文件内容不进 query
    3. 所有 return 都返回 (answer, ids) 元组
    """
    # ==================== 1. 账号状态检查 ====================
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)
    if user_info and user_info.get("status") == "禁用":
        return "【系统安全提示】您的账号已被管理员禁用，您已被强制下线，请联系管理员！", []

    original_query = query
    history_text = user_text if user_text else original_query

    # ==================== 2. 输入护栏 ====================
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg, []

    # ==================== 3. 惰性启动 Worker ====================
    if not query_worker.is_running:
        asyncio.create_task(query_worker.run_loop())
        query_worker.is_running = True
    if not command_worker.is_running:
        asyncio.create_task(command_worker.run_loop())
        command_worker.is_running = True

    # ==================== 4. 待确认工具的处理 ====================
    if session_id in pending and "确认" in query.strip():
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
        return output_guard(result), []

    # ==================== 5. 临时会话文件（只记路径，不读内容） ====================
    # 【架构级修复】每次请求以本次是否携带 temp_file_path 为准：
    # - 带了：说明本次是"上传文件后提问"，用新文件
    # - 没带：说明本次是"纯知识库提问"，清除缓存，避免沿用上一轮
    if temp_file_path:
        _session_temp_files[session_id] = temp_file_path
        current_temp_file = temp_file_path
    else:
        _session_temp_files.pop(session_id, None)
        current_temp_file = None

    # ==================== 6. 检索层：临时文件模式 vs 知识库模式 ====================
    # 【架构决策】两种模式严格隔离：
    #   - 临时文件模式：不检索知识库，让 LLM 通过 analyze_data 工具分析文件
    #   - 知识库模式：正常检索 ChromaDB + BM25
    # 这样从源头消除"参考资料里混入无关内容"导致的 LLM 废话。
    history = memory.get(session_id)[-10:]
    rag_sources = []
    rag_context = ""

    if current_temp_file:
        # 临时文件模式：跳过知识库检索
        print(f"[RAG] 临时文件模式，跳过知识库检索。文件: {current_temp_file}")
        # 不注入任何参考资料，LLM 会看到 history 里的"📎 上传文件：xxx" + 用户问题
        # 自主决定是否调用 analyze_data 工具
    else:
        # 知识库模式
        try:
            from common.rag_v2 import search_knowledge_v2
            rag_result = search_knowledge_v2(query, "")
            if isinstance(rag_result, dict):
                rag_context = rag_result.get("context_text", "")
                rag_sources = rag_result.get("sources", [])
            else:
                rag_context = str(rag_result) if rag_result else ""

            if rag_context:
                MAX_CONTEXT_CHARS = 78000
                if len(rag_context) > MAX_CONTEXT_CHARS:
                    print(f"[RAG] 上下文超长（{len(rag_context)}字符），截断")
                    rag_context = rag_context[:MAX_CONTEXT_CHARS]

                # 【事实陈述】从 sources 提取文件名（去重），陈述"命中来自哪个文件"
                # 不规定 LLM 做什么，只告诉它事实
                source_files = []
                seen_files = set()
                for s in rag_sources:
                    fname = s.get("file_name")
                    if fname and fname not in seen_files:
                        source_files.append(fname)
                        seen_files.add(fname)

                # 【事实陈述】把命中的 CSV 物理路径告诉 LLM
                # 让 LLM 自主决定是否调用 analyze_data 做精确计算
                csv_paths = []
                for fname in source_files:
                    for search_dir in [
                        os.path.join(UPLOAD_DIR, "temp"),
                        UPLOAD_DIR,
                    ]:
                        cand = os.path.join(search_dir, fname)
                        if os.path.exists(cand) and fname.lower().endswith(('.csv', '.xlsx', '.xls')):
                            csv_paths.append(cand)
                            break

                source_header = ""
                if source_files:
                    source_header = f"【企业知识库数据（来源文件：{', '.join(source_files)}）】"
                else:
                    source_header = "【企业知识库数据】"

                file_fact = ""
                if csv_paths:
                    file_fact = f"\n\n【会话可访问的数据文件】{csv_paths[0]}"

                print(f"[RAG] 知识库上下文已注入（{len(rag_context)}字符，来源: {source_files}）")
                query = f"{source_header}\n{rag_context}{file_fact}\n\n【用户问题】\n{query}"
            else:
                print(f"[RAG] 未命中任何相关内容")
        except Exception as e:
            import traceback
            print(f"###RAG检索异常### {traceback.format_exc()}")
            rag_sources = []

    # ==================== 7. 构建消息列表 ====================
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": query})

    # ==================== 8. 角色权限 ====================
    role = user_info.get("role", "viewer") if user_info else "viewer"
    if role not in ROLE_PERMISSIONS:
        role = "manager"

    allowed_tools = TOOLS_METADATA
    if role == "viewer":
        allowed_tools = [t for t in TOOLS_METADATA if t["function"]["name"] not in ["web_search", "fetch_webpage"]]

    image_output = None

    # ==================== 9. 工具调用循环 ====================
    for _ in range(8):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                tools=allowed_tools,
                tool_choice="auto"
            )
        except Exception as e:
            answer = f"模型调用失败: {e}"
            memory.append(session_id, original_query, answer)
            return output_guard(answer), _extract_ids_from_sources(rag_sources)

        msg = response.choices[0].message
        if msg.tool_calls:
            messages.append(msg)

            for tool_call in msg.tool_calls:
                func_name = ""
                arguments = {}
                try:
                    arguments = json.loads(tool_call.function.arguments)
                    func_name = tool_call.function.name
                except Exception as e:
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"函数解析错误: {e}"})
                    continue

                if func_name in ["add_event", "delete_event", "list_events"]:
                    arguments["_tenant"] = memory.get_tenant(session_id)

                # 权限检查
                if role == "viewer" and func_name in ["web_search", "fetch_webpage"]:
                    result = "无权限执行此操作。"
                elif func_name in AVAILABLE_TOOLS:
                    # 【隐式参数注入】analyze_data 的 file_path 后端自动补
                    # 来源优先级：临时文件 > RAG sources 命中的 CSV 物理文件
                    if func_name == "analyze_data" and not arguments.get("file_path"):
                        filled = None
                        # 1. 临时文件
                        if current_temp_file:
                            filled = current_temp_file
                        # 2. 从 RAG sources 找
                        elif rag_sources:
                            for s in rag_sources:
                                fname = s.get("file_name")
                                if not fname:
                                    continue
                                if not fname.lower().endswith(('.csv', '.xlsx', '.xls')):
                                    continue
                                for search_dir in [
                                    os.path.join(UPLOAD_DIR, "temp"),
                                    UPLOAD_DIR,
                                ]:
                                    cand = os.path.join(search_dir, fname)
                                    if os.path.exists(cand):
                                        filled = cand
                                        break
                                if filled:
                                    break

                        if filled:
                            arguments["file_path"] = filled
                            print(f"[Tool] analyze_data 自动补 file_path: {filled}")
                        else:
                            result = "错误：未找到相关数据文件。"
                            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                            continue
                    try:
                        result = AVAILABLE_TOOLS[func_name](**arguments)
                    except Exception as e:
                        result = f"工具执行错误: {e}"
                else:
                    result = f"未找到工具 {func_name}"

                # 审计日志
                simple_log_tool(session_id, original_query, func_name, arguments, result)

                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
        else:
            answer = msg.content
            break
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # ==================== 10. 输出与返回 ====================
    answer = output_guard(answer)
    if image_output:
        answer = answer + "\n\n" + image_output
    memory.append(session_id, history_text, answer)

    context_ids = _extract_ids_from_sources(rag_sources)
    return answer, context_ids

# ==================== 语义提示生成函数（已废弃） ====================
async def generate_plan(user_query, history, client):
    """
    【已废弃】原规划引擎的生成函数。
    当前系统已切换为纯语义 Agent，无需生成任务规划。
    此函数仅为保持兼容性，不再返回任何复杂的步骤依赖提示。
    """
    # 直接返回空，提示架构已变更
    return None

# ================= 各类 API 接口 =================
@app.post("/api/login")
async def api_login(request: LoginRequest):
    user = authenticate(request.username.strip().lower(), request.pin)
    if user and isinstance(user, dict) and user.get("status") == "disabled":
        return {"status": "error", "message": "该账号已被禁用，请联系管理员"}
    if user:
        return {"status": "success", "user": user}
    return {"status": "error", "message": "用户名或密码错误"}

@app.post("/api/chat")
async def api_chat(request: ChatRequest):
    try:
        real_username = request.session_id.split('_')[0] if '_' in request.session_id else request.session_id
        user_info = get_user_info(real_username)
        if user_info and user_info.get("status") == "禁用":
            return {"answer": "【系统提示】您的账号已被禁用，请联系管理员。您已被强制下线。"}
        result = await chat_core(
            request.session_id, request.query, request.user_text,
            _query_worker, _command_worker, _tool_router,
            temp_file_path=getattr(request, 'temp_file_path', None)
        )
        # 【防御】如果 chat_core 返回 None（某条代码路径漏了 return），不要崩溃
        if result is None:
            import traceback
            print(f"###chat_core 返回 None### 这是 bug，请检查 chat_core 分支")
            traceback.print_stack()
            return {"answer": "系统处理异常：内部返回空，请查看后端日志。", "contexts": []}
        answer, retrieved_ids = result
        return {"answer": answer, "contexts": retrieved_ids}
    except Exception as e:
        import traceback
        print(f"###严重Bug### 堆栈详情: {traceback.format_exc()}")
        return {"answer": f"系统处理异常: {e}"}

@app.post("/api/upload_temp")
async def api_upload_temp(file: UploadFile = File(...), session_id: str = Form(...)):
    """
    聊天框临时文件上传接口（与知识库隔离）。
    - 保存到 uploads/temp/{session_id}_{uuid}_{filename}
    - 返回 file_path 供前端后续在 chat 请求里携带
    - 每次上传时清理 > 1 小时的旧临时文件
    """
    import time as _t

    # 清理 > 1 小时的旧文件
    now = _t.time()
    for f in os.listdir(TEMP_UPLOAD_DIR):
        fp = os.path.join(TEMP_UPLOAD_DIR, f)
        try:
            if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > 3600:
                os.remove(fp)
        except Exception:
            pass

    # 保存新文件
    safe_name = os.path.basename(file.filename)
    unique_name = f"{session_id}_{_uuid_mod.uuid4().hex[:8]}_{safe_name}"
    file_path = os.path.join(TEMP_UPLOAD_DIR, unique_name)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        _session_temp_files[session_id] = file_path
        print(f"[Temp] 会话 {session_id} 上传临时文件: {file_path}")
        return {"status": "success", "file_name": safe_name, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": f"临时文件保存失败: {e}"}

    # ==================== 临时文件事实注入 ====================
    # 【设计决策】只陈述事实，不暗示行为。
    # - 如果本次请求带 temp_file_path，更新会话级缓存
    # - 从缓存读取当前会话的临时文件（供 system message 注入）
    if temp_file_path:
        _session_temp_files[session_id] = temp_file_path

    current_temp_file = _session_temp_files.get(session_id)

@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    file_path = os.path.join(os.getcwd(), file.filename)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        ext = os.path.splitext(file_path)[1].lower()
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
            result = ocr_image(file_path)
        elif ext in ['.csv', '.xlsx', '.xls']:
            result = analyze_file(file_path)
        elif ext in ['.wav', '.mp3', '.m4a', '.ogg', '.webm']:
            result = speech_to_text(file_path)
            if "未配置" in result or "凭证无效" in result:
                result = "已接收语音文件。但当前系统后端未成功读取百度API密钥，建议使用【按住说话】按钮。"
        else:
            result = f"已接收文件：{file.filename}（当前仅支持图片/CSV/Excel/音频格式分析）"
        return {"status": "success", "message": result, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": f"上传失败: {e}"}

# 知识库管理接口
@app.get("/api/kb/list")
async def api_kb_list():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")
    try:
        if os.path.exists(rag_file):
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
                # 确保返回的是列表，防止前端解析出错
                files_list = store.get("files", [])
                if not isinstance(files_list, list):
                    files_list = []
                return {"status": "success", "data": files_list}
        return {"status": "success", "data": []}
    except Exception as e:
        # 打印完整报错到后端终端，方便排查
        import traceback
        print(f"###知识库列表Bug### 堆栈详情: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}

@app.post("/api/kb/update_tags")
async def api_kb_update_tags(file_name: str = Form(...), tags: str = Form("")):
    import json
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")
    if os.path.exists(rag_file):
        try:
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
            for f_item in store.get("files", []):
                if f_item.get("file_name") == file_name:
                    f_item["tags"] = tags
                    break
            for key in list(store.get("store", {}).keys()):
                for doc in store["store"][key]:
                    if doc.get("file_name") == file_name:
                        doc["tags"] = tags
            with open(rag_file, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
            return {"status": "success", "message": f"文档 {file_name} 的标签已更新"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "知识库不存在"}

@app.post("/api/kb/index")
async def api_kb_index(file: UploadFile = File(...), tags: str = Form("")):
    """
    异步化上传：把阻塞的索引处理放到后台线程，避免卡死整个后端。
    """
    import asyncio
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        from common.rag_v2 import index_document_v2
        
        # 【关键】用 to_thread 把同步阻塞函数放到后台线程
        msg = await asyncio.to_thread(index_document_v2, file_path, tags)
        
        if "成功" in str(msg):
            return {"status": "success", "message": msg}
        return {"status": "error", "message": msg}
    except Exception as e:
        import traceback
        print(f"###索引Bug### 堆栈详情: {traceback.format_exc()}")
        return {"status": "error", "message": f"索引失败(详细原因): {e}"}

@app.post("/api/kb/delete")
async def api_kb_delete(file_name: str = Form(...)):
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")
    if os.path.exists(rag_file):
        try:
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
            store["files"] = [f for f in store.get("files", []) if f.get("file_name") != file_name]
            for key in list(store.get("store", {}).keys()):
                store["store"][key] = [doc for doc in store["store"][key] if doc.get("file_name") != file_name]
            with open(rag_file, "w", encoding="utf-8") as f:
                json.dump(store, f, ensure_ascii=False, indent=2)
            return {"status": "success", "message": f"文档 {file_name} 已删除"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "知识库不存在"}

@app.get("/api/kb/download")
async def api_kb_download(file_name: str):
    from fastapi.responses import FileResponse
    safe_name = os.path.basename(file_name)
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.exists(file_path):
        return {"status": "error", "message": "文件不存在"}
    return FileResponse(path=file_path, filename=safe_name, media_type='application/octet-stream')

# ================= Admin 用户管理接口 =================
@app.get("/api/users/list")
async def api_users_list():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, real_name, role, department, contact, status FROM users")
    users = [{"username": r[0], "real_name": r[1], "role": r[2], "department": r[3], "contact": r[4], "status": r[5]} for r in cursor.fetchall()]
    conn.close()
    return {"status": "success", "data": users}

@app.post("/api/users/add")
async def api_users_add(username: str = Form(...), pin: str = Form(...), real_name: str = Form(""), role: str = Form("viewer"), department: str = Form(""), contact: str = Form(""), status: str = Form("正常")):
    try:
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, pin, real_name, role, department, contact, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (username, pin, real_name, role, department, contact, status))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "用户添加成功"}
    except Exception as e:
        error_msg = str(e)
        if "UNIQUE constraint failed" in error_msg:
            return {"status": "error", "message": "用户已存在，请更换用户名"}
        return {"status": "error", "message": f"添加失败: {error_msg}"}

@app.post("/api/users/delete")
async def api_users_delete(username: str = Form(...)):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户已删除"}

@app.post("/api/users/update")
async def api_users_update(username: str = Form(...), role: str = Form(...), status: str = Form("正常")):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = ?, status = ? WHERE username = ?", (role, status, username))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户更新成功"}

# ================= 健康、日志、反馈接口 =================
@app.get("/api/health")
async def api_health():
    total_tasks = 0; success_tasks = 0; failed_tasks = 0; total_users = 0; active_users = 0; sorted_tools = {}
    try:
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        conn.close()
    except: pass

    try:
        cutoff_time = (datetime.datetime.now(ZoneInfo("Asia/Shanghai")) - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect("health.db")
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs")
        total_tasks = cursor.fetchone()[0]
        cursor.execute("SELECT status, COUNT(*) FROM logs GROUP BY status")
        for status, count in cursor.fetchall():
            if status == 'success': success_tasks += count
            else: failed_tasks += count
        cursor.execute("SELECT COUNT(DISTINCT username) FROM logs WHERE timestamp >= ?", (cutoff_time,))
        active_users = cursor.fetchone()[0]
        cursor.execute("SELECT tool, COUNT(*) FROM logs GROUP BY tool")
        for tool, count in cursor.fetchall():
            tool_name = tool if tool else "系统操作"
            sorted_tools[tool_name] = count
        conn.close()
    except Exception as e:
        print(f"###DEBUG### 健康查询失败: {e}")

    success_rate = round((success_tasks / total_tasks) * 100, 2) if total_tasks > 0 else 0
    return {"status": "success", "data": {
        "total_tasks": total_tasks, "success_tasks": success_tasks, "failed_tasks": failed_tasks,
        "success_rate": success_rate, "active_users": active_users, "total_users": total_users,
        "total_feedback": 0, "up_feedback": 0, "down_feedback": 0,
        "sorted_tools": [{"tool": k, "count": v} for k, v in sorted_tools.items()]
    }}

@app.get("/api/logs")
async def api_logs():
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plan_log_path = os.path.join(BASE_DIR, "plan_log.json")
    logs = []
    if os.path.exists(plan_log_path):
        with open(plan_log_path, "rb") as f:
            for line in f.read().decode("utf-8", errors="ignore").splitlines():
                try:
                    entry = json.loads(line)
                    ts = entry.get('timestamp', '')[:19]
                    session_id = entry.get('session_id', '')
                    window_name = session_id.split('_', 1)[1] if '_' in session_id else '主对话'
                    logs.append({
                        "timestamp": ts,
                        "username": f"{entry.get('username', 'unknown')}/{window_name}",
                        "role": entry.get('role', 'unknown'),
                        "action": entry.get('tool', '系统操作'),
                        "detail": (entry.get('user_query') or '')[:60],
                        "status": entry.get('status', 'success')
                    })
                except: continue
    logs.reverse()
    return {"status": "success", "data": logs[:100]}

@app.get("/api/logs/export")
async def api_logs_export():
    import csv
    from io import StringIO
    from fastapi.responses import Response
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plan_log_path = os.path.join(BASE_DIR, "plan_log.json")
    output = StringIO(); output.write('\uFEFF')
    writer = csv.writer(output)
    writer.writerow(["时间戳", "操作人/窗口", "角色", "操作行为/内容", "调用工具", "状态"])
    if os.path.exists(plan_log_path):
        with open(plan_log_path, "rb") as f:
            for line in f.read().decode("utf-8", errors="ignore").splitlines():
                try:
                    entry = json.loads(line)
                    session_id = entry.get('session_id', '')
                    window_name = session_id.split('_', 1)[1] if '_' in session_id else '主对话'
                    ts = entry.get('timestamp', '')[:16]
                    detail = (entry.get('user_query') or '')[:60]
                    writer.writerow([ts, f"{entry.get('username')}/{window_name}", entry.get('role', ''), detail, entry.get('tool', ''), entry.get('status', '')])
                except: continue
    filename = f"logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content=output.getvalue().encode('utf-8-sig'), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    try:
        hist = memory.get_history(session_id)
        return {"status": "success", "data": hist if hist else []}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/feedback")
async def api_feedback(session_id: str = Form(...), feedback_type: str = Form(...), feedback_text: str = Form("")):
    try:
        conn = sqlite3.connect("feedback.db")
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, feedback_type TEXT, feedback_text TEXT, time TEXT)''')
        time_str = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO feedback (session_id, feedback_type, feedback_text, time) VALUES (?, ?, ?, ?)", (session_id, feedback_type, feedback_text, time_str))
        conn.commit(); conn.close()
        return {"status": "success", "message": "反馈已记录"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/status")
async def api_status():
    workers = []
    try:
        if _query_worker: workers.append(_query_worker.get_stats())
        if _command_worker: workers.append(_command_worker.get_stats())
        return {"status": "success", "data": workers}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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