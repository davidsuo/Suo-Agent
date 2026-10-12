# common/main.py

"""
common/main.py - 核心聊天逻辑与 API 路由

================================================================================
RAG 架构组件映射（标准 RAG Architecture）
================================================================================

【1. 知识库 Knowledge Base】
    职责：系统的外部数据存储库
    位置：
      - uploads/            原始上传文件
      - rag_data.json       知识库元数据（files/schema/store）
      - chroma_db/          向量数据库（ChromaDB 持久化）

【2. 检索器 Retriever】
    职责：在知识库中搜索相关数据的 AI 模型
    位置：
      - common/rag_v2.py    双路混合检索实现
      - common/main.py::_retrieve_background    物理层检索封装

【3. 集成层 Integration Layer】
    职责：协调 RAG 的整体功能
    位置：
      - common/main.py::_select_capabilities   能力筛选
      - common/main.py::_discover_data_sources 数据源目录注入
      - common/main.py::chat_core_stream       主流程集成

【4. 生成器 Generator】
    职责：根据用户查询和检索数据创建输出
    位置：
      - common/main.py::client                  DeepSeek LLM 客户端
      - common/main.py::SYSTEM_PROMPT           LLM 能力边界定义
      - common/main.py::chat_core_stream 的工具执行循环   LLM 决策 -> 工具 -> 结果

【其他组件】
  · 排名器 Ranker：RRF 融合 + 阈值筛选（rag_v2.py::search_knowledge_v2）
  · 输出处理 Output Handler：output_guard + 物理层前缀（main.py::chat_core_stream）

模块职责：
模块职责（US-13 模块化后）：
1. FastAPI 路由定义（api_chat / api_login / ...）
2. 启动事件（startup_event）+ DB 初始化
3. chat_core_stream：薄转发到 orchestrator.run_orchestration

⚠️ 编排逻辑已抽离：
   - common/orchestrator.py（主流程编排 / 语义分析 / 背景检索）

架构说明：
- 前 置：guardrails 安全过滤 → 用户权限 → 临时文件处理
- 决策层：LLM 自主决策调什么工具
- 执行层：工具执行 + 重试 + 审计日志
- 后 置：输出校验 → 记忆写入 → SSE 流式拼接
"""

import sys, os, json, asyncio, re, datetime, time
import threading
from typing import Optional
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import shutil
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

import sqlite3
from zoneinfo import ZoneInfo

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
from common.auth import authenticate, get_user_info, is_tool_allowed, ROLE_PERMISSIONS, init_users_db
from common.memory import memory
# 【US-13】主流程编排已抽离到独立模块
from common.orchestrator import run_orchestration

app = FastAPI()
DIST_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend', 'dist')

# 【Render Disk 适配】优先用环境变量 UPLOAD_DIR（指向 Persistent Disk）
# 本地开发时环境变量不存在，走默认路径
UPLOAD_DIR = os.getenv(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

import uuid as _uuid_mod
TEMP_UPLOAD_DIR = os.path.join(UPLOAD_DIR, "temp")
os.makedirs(TEMP_UPLOAD_DIR, exist_ok=True)
_session_temp_files: dict = {}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "https://suo-agent.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str
    pin: str


class ChatRequest(BaseModel):
    session_id: str
    query: str
    user_text: Optional[str] = None
    temp_file_path: Optional[str] = None


def init_db():
    """初始化示例 SQLite 数据库（sample.db）—— 幂等，自动补全空库"""
    db_path = os.path.join(UPLOAD_DIR, "sample.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    # 1. 无论文件是否存在，都确保建表
    cursor.execute('''CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY, name TEXT, position TEXT, salary INTEGER)''')
    # 2. 如果表为空，补全示例数据
    cursor.execute("SELECT COUNT(*) FROM employees")
    if cursor.fetchone()[0] == 0:
        sample_data = [(1, "张三", "工程师", 60000), (2, "李四", "产品经理", 75000),
                       (3, "王五", "设计师", 55000), (4, "赵六", "数据分析师", 68000)]
        cursor.executemany("INSERT OR REPLACE INTO employees VALUES (?,?,?,?)", sample_data)
    conn.commit()
    conn.close()


def init_health_db():
    """初始化健康日志数据库（health.db），用于记录工具调用审计日志"""
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "health.db"))
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, session_id TEXT, username TEXT, role TEXT,
        tool TEXT, query TEXT, result TEXT, status TEXT)''')
    conn.commit()
    conn.close()

def _cleanup_old_charts(days: int = 30):
    """
    【故事 4】清理 uploads/charts/ 目录下超过指定天数的旧图表。

    Args:
        days: 保留天数，默认 30 天
    """
    import time as _t
    charts_dir = os.path.join(UPLOAD_DIR, "charts")
    if not os.path.exists(charts_dir):
        return

    now = _t.time()
    cutoff_seconds = days * 24 * 3600
    cleaned = 0
    failed = 0

    for fname in os.listdir(charts_dir):
        if not fname.endswith(".png"):
            continue
        fpath = os.path.join(charts_dir, fname)
        try:
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > cutoff_seconds:
                os.remove(fpath)
                cleaned += 1
        except Exception as e:
            failed += 1
            print(f"###图片清理### 删除失败 {fname}: {e}")

    if cleaned > 0 or failed > 0:
        print(f"###图片清理### 清理完成 | 删除 {cleaned} 个 | 失败 {failed} 个 | 保留天数 {days}")
    else:
        print(f"###图片清理### 无过期图表 | 保留天数 {days}")

from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker

_query_worker = None
_command_worker = None
_tool_router = None


def set_workers(query_worker, command_worker, tool_router):
    """全局注册 QueryWorker、CommandWorker 和工具路由器"""
    global _query_worker, _command_worker, _tool_router
    _query_worker = query_worker
    _command_worker = command_worker
    _tool_router = tool_router


@app.on_event("startup")
async def startup_event():
    """
    FastAPI 启动事件：初始化数据库、清理旧图表、加载 RAG 模型、启动 Worker 循环
    """
    global _query_worker, _command_worker, _tool_router
    init_users_db()
    init_db()
    init_health_db()
    _cleanup_old_charts(days=30)

    # 【核心修复】强制在启动时加载 RAG 和 Reranker 模型，消灭首次请求的冷启动延迟
    # 注意：必须先清空 sys.modules 缓存，否则 Python 会跳过模块的顶层加载代码
    print("###启动加载### 正在初始化 RAG 向量模型和 Reranker 模型...")
    if 'common.rag_v2' in sys.modules:
        del sys.modules['common.rag_v2']
    import common.rag_v2
    print("###启动加载### RAG 模块初始化完成！")

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

    # 【US-03 修复】在服务启动时立即启动 Worker 循环，确保状态监控正常显示
    if _query_worker and not _query_worker.is_running:
        asyncio.create_task(_query_worker.run_loop())
        _query_worker.is_running = True
    if _command_worker and not _command_worker.is_running:
        asyncio.create_task(_command_worker.run_loop())
        _command_worker.is_running = True

    print("✅ FastAPI 初始化完成")

async def chat_core_stream(session_id: str, query: str, user_text: str = None,
    query_worker=None, command_worker=None, TOOL_ROUTER=None,
    image_base64: str = None, temp_file_path: str = None):
    """
    【US-13】编排逻辑已抽离到 common/orchestrator.py。
    本函数只做薄转发，保持 SSE 流式接口不变。
    """
    async for chunk in run_orchestration(
        session_id, query, user_text,
        query_worker, command_worker, TOOL_ROUTER,
        image_base64, temp_file_path
    ):
        yield chunk

# ================= API 接口 =================
@app.post("/api/login")
async def api_login(request: LoginRequest):
    """用户登录接口，验证用户名和 PIN 码"""
    user = authenticate(request.username.strip().lower(), request.pin)
    if user and isinstance(user, dict) and user.get("status") == "disabled":
        return {"status": "error", "message": "该账号已被禁用，请联系管理员"}
    if user:
        return {"status": "success", "user": user}
    return {"status": "error", "message": "用户名或密码错误"}


@app.post("/api/chat")
async def api_chat(request: ChatRequest):
    """
    核心聊天接口，返回 SSE 流式响应。
    包含前置安全拦截，防止已禁用账号进行对话。
    """
    real_username = request.session_id.split('_')[0] if '_' in request.session_id else request.session_id
    user_info = get_user_info(real_username)

    # 【核心修复】禁用或不存在用户，返回 SSE 流格式的错误信息，防止前端流式解析卡死
    if not user_info or user_info.get("status") == "禁用":
        async def block_stream():
            msg = "【系统安全提示】您的账号已被管理员禁用或不存在。"
            yield f"data: {json.dumps({'type': 'answer', 'content': msg}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(block_stream(), media_type="text/event-stream")

    try:
        generator = chat_core_stream(
            request.session_id, request.query, request.user_text,
            _query_worker, _command_worker, _tool_router,
            temp_file_path=getattr(request, 'temp_file_path', None)
        )

        async def event_stream():
            async for chunk in generator:
                yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
    except Exception as e:
        import traceback
        print(f"###严重Bug### {traceback.format_exc()}")
        # 【US-09 修复】提前捕获 e 为普通变量，避免异步生成器执行时 e 已被 Python 清除
        error_msg = str(e)
        async def error_stream():
            yield f"data: {json.dumps({'type': 'answer', 'content': f'系统处理异常: {error_msg}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")


@app.post("/api/upload_temp")
async def api_upload_temp(file: UploadFile = File(...), session_id: str = Form(...)):
    """上传临时文件接口，文件保存在 temp 目录，有效期 1 小时"""
    import time as _t
    now = _t.time()
    for f in os.listdir(TEMP_UPLOAD_DIR):
        fp = os.path.join(TEMP_UPLOAD_DIR, f)
        try:
            if os.path.isfile(fp) and (now - os.path.getmtime(fp)) > 3600:
                os.remove(fp)
        except Exception:
            pass
    safe_name = os.path.basename(file.filename)
    unique_name = f"{session_id}_{_uuid_mod.uuid4().hex[:8]}_{safe_name}"
    file_path = os.path.join(TEMP_UPLOAD_DIR, unique_name)
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        _session_temp_files[session_id] = file_path
        return {"status": "success", "file_name": safe_name, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": f"临时文件保存失败: {e}"}


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)):
    """通用文件上传接口，支持图片 OCR、CSV 分析、语音转写"""
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
        else:
            result = f"已接收文件：{file.filename}"
        return {"status": "success", "message": result, "file_path": file_path}
    except Exception as e:
        return {"status": "error", "message": f"上传失败: {e}"}


@app.get("/api/kb/list")
async def api_kb_list():
    """获取知识库文件列表"""
    rag_file = os.path.join(UPLOAD_DIR, "rag_data.json")
    try:
        if os.path.exists(rag_file):
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
                files_list = store.get("files", [])
                return {"status": "success", "data": files_list if isinstance(files_list, list) else []}
        return {"status": "success", "data": []}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/kb/update_tags")
async def api_kb_update_tags(file_name: str = Form(...), tags: str = Form("")):
    """更新知识库文档的标签"""
    rag_file = os.path.join(UPLOAD_DIR, "rag_data.json")
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
            return {"status": "success", "message": f"标签已更新"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "知识库不存在"}


@app.post("/api/kb/index")
async def api_kb_index(file: UploadFile = File(...), tags: str = Form("")):
    """上传文档并建立索引，调用 RAG V2 的分块和向量化逻辑"""
    import asyncio
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_path = os.path.join(UPLOAD_DIR, os.path.basename(file.filename))
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        from common.rag_v2 import index_document_v2
        msg = await asyncio.to_thread(index_document_v2, file_path, tags)
        if "成功" in str(msg):
            return {"status": "success", "message": msg}
        return {"status": "error", "message": msg}
    except Exception as e:
        import traceback
        print(f"###索引Bug### {traceback.format_exc()}")
        return {"status": "error", "message": f"索引失败: {e}"}


@app.post("/api/kb/delete")
async def api_kb_delete(file_name: str = Form(...)):
    """从知识库中删除文档"""
    rag_file = os.path.join(UPLOAD_DIR, "rag_data.json")
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
    """下载知识库中的文档"""
    safe_name = os.path.basename(file_name)
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.exists(file_path):
        return {"status": "error", "message": "文件不存在"}
    return FileResponse(path=file_path, filename=safe_name, media_type='application/octet-stream')


@app.get("/api/users/list")
async def api_users_list():
    """获取用户列表（管理员）"""
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT username, real_name, role, department, contact, status FROM users")
    users = [{"username": r[0], "real_name": r[1], "role": r[2], "department": r[3], "contact": r[4], "status": r[5]} for r in cursor.fetchall()]
    conn.close()
    return {"status": "success", "data": users}

@app.get("/api/users/roles")
async def api_users_roles():
    """返回系统中所有可用角色及其中文名，供前端动态渲染（US-07）

    数据源：common/auth.py 的 ROLE_PERMISSIONS（权限真源）
    中文名：common/auth.py 的 ROLE_DISPLAY_NAMES（显示真源）
    """
    from common.auth import ROLE_PERMISSIONS, ROLE_DISPLAY_NAMES
    roles = [
        {"value": role, "label": ROLE_DISPLAY_NAMES.get(role, role)}
        for role in ROLE_PERMISSIONS.keys()
    ]
    return {"status": "success", "data": roles}


@app.post("/api/users/add")
async def api_users_add(username: str = Form(...), pin: str = Form(...), real_name: str = Form(""), role: str = Form("viewer"), department: str = Form(""), contact: str = Form(""), status: str = Form("正常")):
    """添加新用户（管理员）"""
    try:
        conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (username, pin, real_name, role, department, contact, status) VALUES (?, ?, ?, ?, ?, ?, ?)", (username, pin, real_name, role, department, contact, status))
        conn.commit()
        conn.close()
        return {"status": "success", "message": "用户添加成功"}
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            return {"status": "error", "message": "用户已存在"}
        return {"status": "error", "message": f"添加失败: {e}"}


@app.post("/api/users/delete")
async def api_users_delete(username: str = Form(...)):
    """删除用户（管理员）"""
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户已删除"}


@app.post("/api/users/update")
async def api_users_update(username: str = Form(...), role: str = Form(...), status: str = Form("正常")):
    """更新用户角色或状态（管理员）"""
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = ?, status = ? WHERE username = ?", (role, status, username))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户更新成功"}


@app.get("/api/health")
async def api_health():
    """获取系统健康指标（总任务数、成功率、活跃用户、工具调用分布）"""
    total_tasks = 0; success_tasks = 0; failed_tasks = 0; total_users = 0; active_users = 0; sorted_tools = {}
    try:
        conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        conn.close()
    except: pass
    try:
        cutoff_time = (datetime.datetime.now(ZoneInfo("Asia/Shanghai")) - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "health.db"))
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
async def api_logs(session_id: str = ""):
    """获取操作日志列表（带观察者权限校验，直接查库）"""
    # 【US-06 安全修复】必须携带 session_id，否则直接拒绝
    if not session_id or not session_id.strip():
        return {"status": "error", "message": "缺少 session_id 参数，无权访问"}

    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    try:
        _conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
        _cur = _conn.cursor()
        _cur.execute("SELECT role FROM users WHERE username = ?", (real_username,))
        _row = _cur.fetchone()
        _conn.close()
        if not _row:
            return {"status": "error", "message": "用户不存在，无权访问"}
        if _row[0] == "viewer":
            return {"status": "error", "message": "无权限查看系统日志，请联系管理员"}
    except Exception as e:
        print(f"###api_logs### 权限校验异常: {e}")
        return {"status": "error", "message": "权限校验失败"}

    plan_log_path = os.path.join(UPLOAD_DIR, "plan_log.json")
    logs = []
    if os.path.exists(plan_log_path):
        with open(plan_log_path, "rb") as f:
            for line in f.read().decode("utf-8", errors="ignore").splitlines():
                try:
                    entry = json.loads(line)
                    ts = entry.get('timestamp', '')[:19]
                    session_id_entry = entry.get('session_id', '')
                    window_name = session_id_entry.split('_', 1)[1] if '_' in session_id_entry else '主对话'
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
async def api_logs_export(session_id: str = ""):
    """导出操作日志为 CSV 文件（带观察者权限校验）"""
    # 【US-06 安全修复】必须携带 session_id，否则直接拒绝
    if not session_id or not session_id.strip():
        return {"status": "error", "message": "缺少 session_id 参数，无权访问"}

    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    try:
        _conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
        _cur = _conn.cursor()
        _cur.execute("SELECT role FROM users WHERE username = ?", (real_username,))
        _row = _cur.fetchone()
        _conn.close()
        if not _row:
            return {"status": "error", "message": "用户不存在，无权访问"}
        if _row[0] == "viewer":
            return {"status": "error", "message": "无权限导出系统日志"}
    except Exception as e:
        print(f"###api_logs_export### 权限校验异常: {e}")
        return {"status": "error", "message": "权限校验失败"}

    import csv
    from io import StringIO
    from fastapi.responses import Response
    plan_log_path = os.path.join(UPLOAD_DIR, "plan_log.json")
    output = StringIO(); output.write('\uFEFF')
    writer = csv.writer(output)
    writer.writerow(["时间戳", "操作人/窗口", "角色", "操作行为/内容", "调用工具", "状态"])
    if os.path.exists(plan_log_path):
        with open(plan_log_path, "rb") as f:
            for line in f.read().decode("utf-8", errors="ignore").splitlines():
                try:
                    entry = json.loads(line)
                    session_id_entry = entry.get('session_id', '')
                    window_name = session_id_entry.split('_', 1)[1] if '_' in session_id_entry else '主对话'
                    ts = entry.get('timestamp', '')[:16]
                    detail = (entry.get('user_query') or '')[:60]
                    writer.writerow([ts, f"{entry.get('username')}/{window_name}", entry.get('role', ''), detail, entry.get('tool', ''), entry.get('status', '')])
                except: continue
    filename = f"logs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content=output.getvalue().encode('utf-8-sig'), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={filename}"})

@app.get("/api/test/user-roles")
async def api_test_user_roles():
    """仅供回归测试使用：返回 username → role 映射。
    
    返回的信息（用户名+角色）本就在 /api/users/list 中可得，
    这里只做简化封装，避免脚本硬编码用户列表。
    """
    try:
        _conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
        _cur = _conn.cursor()
        _cur.execute("SELECT username, role FROM users WHERE status = '正常'")
        rows = _cur.fetchall()
        _conn.close()
        return {"status": "success", "data": {u: r for u, r in rows}}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/history/{session_id}")
async def get_history(session_id: str):
    """获取指定会话的历史对话记录"""
    try:
        hist = memory.get_history(session_id)
        return {"status": "success", "data": hist if hist else []}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/history/clear")
async def api_history_clear(session_id: str = Form(...)):
    """清空指定会话的所有历史消息"""
    try:
        memory.clear(session_id)
        print(f"###历史清空### session={session_id}")
        return {"status": "success", "message": "已清空会话历史"}
    except Exception as e:
        print(f"###历史清空### 失败: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/feedback")
async def api_feedback(session_id: str = Form(...), feedback_type: str = Form(...), feedback_text: str = Form("")):
    """用户反馈接口（点赞/点踩）"""
    try:
        conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "feedback.db"))
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
    """获取 QueryWorker 和 CommandWorker 的运行状态"""
    workers = []
    try:
        if _query_worker: workers.append(_query_worker.get_stats())
        if _command_worker: workers.append(_command_worker.get_stats())
        return {"status": "success", "data": workers}
    except Exception as e:
        return {"status": "error", "message": str(e)}

CHARTS_DIR = os.path.join(UPLOAD_DIR, "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)
app.mount("/charts", StaticFiles(directory=CHARTS_DIR), name="charts")

# 【新增】挂载图像生成目录，供 generate_image 返回的 URL 正常渲染
IMAGES_DIR = os.path.join(UPLOAD_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)
app.mount("/images", StaticFiles(directory=IMAGES_DIR), name="images")

# 【US-10】带 hash 的静态资源永久缓存（内容变则 hash 变，URL 变，不会加载到旧版本）
from starlette.staticfiles import StaticFiles as _BaseStaticFiles

class _CacheControlledStaticFiles(_BaseStaticFiles):
    """给带 hash 的静态资源加 Cache-Control: immutable"""
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

if os.path.exists(DIST_DIR):
    app.mount(
        "/assets",
        _CacheControlledStaticFiles(directory=os.path.join(DIST_DIR, "assets")),
        name="assets",
    )

    # 【US-10】index.html 禁用缓存，保证用户总是拿到最新版本
    _HTML_NO_CACHE_HEADERS = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }

    @app.get("/")
    async def serve_react():
        return FileResponse(
            os.path.join(DIST_DIR, "index.html"),
            headers=_HTML_NO_CACHE_HEADERS,
        )

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api"):
            return {"detail": "Not Found"}
        file_path = os.path.join(DIST_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(
            os.path.join(DIST_DIR, "index.html"),
            headers=_HTML_NO_CACHE_HEADERS,
        )