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
      - common/main.py::_route_query           语义路由
      - common/main.py::_build_schema_hint     数据文件 schema 注入
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

# 优先从环境变量读取 API Key，支持在 Render 环境变量中配置
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

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
    db_path = os.path.join(UPLOAD_DIR, "sample.db")
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


def write_log_to_db(entry):
    try:
        conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "health.db"))
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
    _cleanup_old_charts(days=30)
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


# ================= V3 系统提示 =================
SYSTEM_PROMPT = """
你是一个企业级AI智能助手。你拥有工具调用能力，请根据用户意图自主决策调用哪些工具。

【能力边界】
- **系统已自动检索企业知识库，背景资料已注入 system prompt**。请优先基于背景资料回答。
- 如果背景资料不足以回答，可调用 search_knowledge 做**进一步**检索。
- 涉及数据文件的统计、趋势、聚合、筛选 → 使用 aggregate
- 涉及日程 → 使用 list_events / add_event / delete_event
- 涉及实时信息 → 使用 web_search
- 涉及文件概况 → 使用 analyze_file
- 涉及图表可视化（折线图/柱状图/饼图等）→ 使用 generate_chart
如果知识库和工具都无法解决，请坦诚告知用户。

【few-shot 参考】
用户问："各月咖啡销售趋势"
思考：这需要按月统计销售额。我应该调用 aggregate，传入 group_by="month"。
调用：aggregate(file_name="coffee_sales.csv", filter_json="{}", agg_column="price", agg_func="sum", group_by="month")

用户问："将趋势绘制成柱状图"
思考：这需要生成柱状图。我应该调用 generate_chart，传入聚合参数。
调用：generate_chart(file_name="coffee_sales.csv", filter_json="{}", agg_column="price", agg_func="sum", group_by="month", chart_type="bar", title="各月咖啡销售柱状图")

【输出风格与结构模板】
为了确保最佳的可视化报告体验，你的回答必须严格遵守以下结构（使用 Markdown）：
1. 首先输出一级标题：`# 各月咖啡销售趋势柱状图`（或类似包含“柱状图/图表”的标题）
2. 无需手动写图片，系统会自动在该标题下方插入图表。
3. 接着输出一级标题：`# 各月咖啡销售趋势分析`
4. 在此标题下方，输出 Markdown 表格（包含“月份”、“销售额”、“订单数(杯)”等列）。
5. 最后输出你的文字洞察分析。
严禁直接把原始 JSON 或文本格式的工具返回结果直接粘贴给用户！你必须对数据进行提炼和总结。

【工具说明】
- `execute_python` 是纯计算沙箱，不支持绘图库。画图请用 `generate_chart`。
- `generate_chart` 支持数据文件的图表生成，会自动读取文件、聚合、渲染彩色图片。
"""


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
        "result": str(result)[:300], "status": status, "mode": "semantic_agent_v3"
    }
    try:
        with open(os.path.join(UPLOAD_DIR, "plan_log.json"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        write_log_to_db(entry)
    except Exception as e:
        print(f"[审计] 写入失败: {e}", flush=True)


log_lock = threading.Lock()


def _extract_ids_from_text(text: str) -> list:
    if not text:
        return []
    ids, seen = [], set()
    for m in re.findall(r'\[([A-Z]+-\d+)\]', str(text)):
        if m not in seen:
            ids.append(m)
            seen.add(m)
            if len(ids) >= 5:
                break
    return ids

async def _route_query(query: str) -> str:
    """
    语义路由：判断用户问题的类型。
    """
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是一个语义路由器。根据用户问题，判断其类型，"
                        "只返回以下 5 个类别之一，不要任何解释：\n\n"
                        "- knowledge: 企业内部的故障排查、IT 支持、文档查询、"
                        "FAQ、流程规定、行政政策\n"
                        "- data: 对数据文件（CSV/Excel）进行统计、查询、"
                        "聚合、可视化\n"
                        "- schedule: 日程管理（查询/添加/删除日程、会议提醒）\n"
                        "- realtime: 需要实时互联网信息（新闻、天气、股价）\n"
                        "- chitchat: 闲聊、常识问答、计算题、不涉及企业专属信息"
                    )
                },
                {"role": "user", "content": query}
            ],
            max_tokens=10,
            temperature=0
        )
        raw = resp.choices[0].message.content.strip().lower()
        for c in ["knowledge", "data", "schedule", "realtime", "chitchat"]:
            if c in raw:
                print(f"###路由### '{query[:30]}...' -> {c}")
                return c
        print(f"###路由### 未识别类别 '{raw}'，默认 chitchat")
        return "chitchat"
    except Exception as e:
        print(f"###路由### 失败: {e}，保守走 knowledge")
        return "knowledge"

# ================= V3：从 rag_data.json 构建 schema 提示 ====================
def _build_schema_hint() -> str:
    """
    【V3 核心】从 rag_data.json 读取所有文件的 schema，构建给 LLM 的数据描述。
    """
    rag_file = os.path.join(UPLOAD_DIR, "rag_data.json")

    store = {}

    if not os.path.exists(rag_file):
        print("###schema### rag_data.json 不存在，跳过 schema 注入")
        return ""

    try:
        with open(rag_file, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception as e:
        print(f"###schema### 读取 rag_data.json 失败: {e}")
        return ""

    lines = ["【可用数据文件】", "⚠️ 注意：以下仅为文件概况，因为你不知道具体数值，任何涉及数值的查询都必须调用 `aggregate` 或 `generate_chart` 工具获取真实数据："]
    for item in store.get("files", []):
        fname = item.get("file_name", "")
        schema = item.get("schema")
        if not schema:
            continue
        lines.append(f"\n📄 {fname}（{schema.get('row_count', '?')} 行）")
        for col in schema.get("columns", []):
            col_name = col.get("name")
            col_type = col.get("type")
            type_hint = {
                "date": "日期",
                "numeric": "数值",
                "category": "分类",
                "text": "文本",
            }.get(col_type, col_type)
            lines.append(f"  - {col_name}（{type_hint}）")

    if len(lines) == 2:
        return ""

    print(f"###schema### 构建成功，包含 {len(lines) - 2} 列信息")
    return "\n".join(lines)

def _retrieve_background(query: str) -> dict:
    """
    【检索基础设施化】物理层无条件执行企业知识库检索。
    """
    from common.rag_v2 import search_knowledge_v2
    try:
        result = search_knowledge_v2(query, "")
        if isinstance(result, dict):
            text = result.get("context_text", "")
            sources = result.get("sources", [])
            ids = [s.get("doc_id") for s in sources if s.get("doc_id")]
            if text and len(text) > 8000:
                text = text[:8000] + "\n...（背景资料过长，已截断）"
            return {"text": text, "ids": ids}
    except Exception as e:
        print(f"###背景检索### 失败: {e}")
    return {"text": "", "ids": []}

async def chat_core_stream(session_id: str, query: str, user_text: str = None,
    query_worker=None, command_worker=None, TOOL_ROUTER=None,
    image_base64: str = None, temp_file_path: str = None):
    """
    V3 架构（最终稳定版）：流式输出 + 工具调用 + 异常捕获 + 日期注入
    """
    # ① 前置管道
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)
    if user_info and user_info.get("status") == "禁用":
        yield json.dumps({"type": "status", "content": "【系统安全提示】您的账号已被管理员禁用。"}, ensure_ascii=False)
        return

    original_query = query
    history_text = user_text if user_text else original_query

    tool_trace = []
    source_prefix = ""

    is_safe, err_msg = input_guard(query)
    if not is_safe:
        yield json.dumps({"type": "status", "content": err_msg}, ensure_ascii=False)
        return

    if not query_worker.is_running:
        asyncio.create_task(query_worker.run_loop())
        query_worker.is_running = True
    if not command_worker.is_running:
        asyncio.create_task(command_worker.run_loop())
        command_worker.is_running = True

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
        yield json.dumps({"type": "answer", "content": output_guard(result), "contexts": []}, ensure_ascii=False)
        return

    if temp_file_path:
        _session_temp_files[session_id] = temp_file_path
        current_temp_file = temp_file_path
    else:
        _session_temp_files.pop(session_id, None)
        current_temp_file = None

    # 时间快路径
    if any(kw in query for kw in ["现在几点", "现在时间", "几点了", "什么时间", "当前时间"]):
        time_result = get_current_time()
        answer = f"现在是 {time_result}（北京时间）。"
        simple_log_tool(session_id, original_query, "get_current_time", {}, time_result)
        memory.append(session_id, history_text, answer)
        yield json.dumps({"type": "answer", "content": output_guard(answer), "contexts": []}, ensure_ascii=False)
        return

    # 记忆装载
    history = memory.get(session_id)[-20:]

    # ② 语义路由
    yield json.dumps({"type": "status", "content": "正在分析问题类型..."}, ensure_ascii=False)
    route = await _route_query(query)
    system_content = SYSTEM_PROMPT

    # 数据类注入 schema
    if route == "data":
        schema_hint = _build_schema_hint()
        if schema_hint:
            system_content = SYSTEM_PROMPT + "\n\n" + schema_hint
            print(f"###schema### 注入 {len(schema_hint)} 字符的数据 schema")
        else:
            print(f"###schema### 无可用 schema，跳过")
    else:
        print(f"###schema### 跳过（路由={route}）")

    # 物理层数据来源前缀
    if current_temp_file:
        raw_name = os.path.basename(current_temp_file)
        if raw_name.startswith(session_id + "_"):
            raw_name = raw_name[len(session_id) + 1:]
        if len(raw_name) > 9 and raw_name[8] == '_':
            raw_name = raw_name[9:]
        source_prefix = f"根据上传文件 {raw_name} 和工具返回的真实数据，"
    elif route == "knowledge":
        source_prefix = "根据企业知识库文档和工具返回的真实数据，"
    elif route == "data":
        source_prefix = "根据数据文件分析结果，"
    elif route == "realtime":
        source_prefix = "根据实时信息，"

    # 知识类背景检索
    bg = {"text": "", "ids": []}
    if route == "knowledge":
        yield json.dumps({"type": "status", "content": "正在检索企业知识库..."}, ensure_ascii=False)
        bg = _retrieve_background(query)
        if bg["text"]:
            system_content += (
                f"\n\n【企业知识库背景资料】\n{bg['text']}\n\n"
                "【背景资料说明】以上是系统自动检索到的企业知识库内容。"
                "请优先基于这些资料回答用户问题。"
                "【极其重要】如果背景资料为空，或资料与用户问题完全不相关，"
                "请务必直接回答：“根据企业知识库文档，未能找到关于该问题的具体说明。”"
                "绝对禁止基于无关资料强行推测或编造答案。"
            )
            print(f"###背景检索### 命中 {len(bg['ids'])} 个 ID: {bg['ids']}")
        else:
            print(f"###背景检索### 无相关命中")

    system_content += "\n\n【回答要求】请不要在回答开头写任何关于数据来源的说明。直接以'以下是...'开头。系统会自动为你添加前缀。"

    # 【核心修复】强制注入当前日期，解决 LLM 编造日期的问题
    import datetime
    from zoneinfo import ZoneInfo
    # 强制使用上海时区，避免 UTC 时间导致日期错乱
    current_date_str = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日 %H:%M:%S")
    system_content += f"\n\n【系统时间信息】当前系统时间是 {current_date_str}。请以此时间为准，不要依赖你的内部知识库或历史记忆。"

    # ③ 角色权限过滤
    role = user_info.get("role", "viewer") if user_info else "viewer"
    if role not in ROLE_PERMISSIONS:
        role = "manager"

    allowed_tools = TOOLS_METADATA
    if role == "viewer":
        allowed_tools = [t for t in TOOLS_METADATA
                         if t["function"]["name"] not in ["web_search", "fetch_webpage"]]

    # ④ 构建 messages
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": query})

    image_output = None
    collected_sources = list(bg["ids"])

    MAX_ITERATIONS = 8
    MAX_RETRIES = 2

    # ⑤ LLM 决策 + 工具执行循环
    for iteration in range(MAX_ITERATIONS):
        t_llm = time.time()
        response = None
        last_error = None
        for llm_retry in range(3):
            try:
                response = client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    tools=allowed_tools,
                    tool_choice="auto"
                )
                if response is None or not getattr(response, "choices", None):
                    last_error = "API 返回空响应"
                    print(f"###LLM重试### 第 {llm_retry+1} 次：API 返回空，1s 后重试")
                    time.sleep(1)
                    continue
                break
            except Exception as e:
                import traceback
                last_error = e
                print(f"###LLM重试### 第 {llm_retry+1} 次失败: {type(e).__name__}: {e}")
                traceback.print_exc()  # 打印详细堆栈，方便定位网络/API Key问题
                if llm_retry < 2:
                    time.sleep(1)

        if response is None or not getattr(response, "choices", None):
            answer = f"模型调用失败（已重试 3 次）: {last_error}，请检查本地网络或 API Key 配置。"
            print(f"###严重Bug### 模型调用彻底失败: {last_error}")
            memory.append(session_id, original_query, answer)
            yield json.dumps({"type": "answer", "content": output_guard(answer), "contexts": collected_sources}, ensure_ascii=False)
            return

        t_llm_cost = round(time.time() - t_llm, 3)
        msg = response.choices[0].message
        tool_trace.append({
            "iteration": iteration, "stage": "llm",
            "cost_seconds": t_llm_cost,
            "has_tool_calls": bool(msg.tool_calls),
        })

        if not msg.tool_calls:
            answer = msg.content
            # 【容错修复】防止 LLM 返回空内容导致程序崩溃
            if not answer or not answer.strip():
                print(f"###警告### LLM 返回了空内容，触发重试")
                if iteration < MAX_ITERATIONS - 1:
                    continue
                answer = "抱歉，模型未能生成有效回答，请稍后重试。"

            data_intent_words = [
                "趋势", "统计", "汇总", "销售额", "各月", "季度", "同比", "环比", "排名",
                "画图", "绘制", "生成图", "折线图", "柱状图", "饼图", "可视化", "图表", "作图",
                "收入", "利润", "订单", "销量", "业绩", "明细", "对比", "分布"
            ]
            knowledge_intent_words = [
                "怎么处理", "怎么办", "如何解决", "如何恢复", "怎样修复", "如何处理",
                "报错", "故障", "失效", "打不开", "连不上", "没反应", "不响应",
                "脱机", "崩溃", "蓝屏", "死机", "闪退", "异常",
                "流程", "规定", "手册", "指南", "政策", "怎么申请", "如何申请"
            ]
            has_data_intent = any(w in original_query for w in data_intent_words)
            has_knowledge_intent = any(w in original_query for w in knowledge_intent_words)

            if (has_data_intent or has_knowledge_intent) and iteration == 0:
                messages.append({
                    "role": "system",
                    "content": (
                        "你刚才的回答没有调用任何工具。请重新审视用户的问题，"
                        "判断是否需要调用工具。可用工具及其适用场景见工具描述，"
                        "请根据用户意图自主选择最合适的工具。"
                    )
                })
                continue

            # 出最终报告前发状态
            yield json.dumps({"type": "status", "content": "正在生成最终报告..."}, ensure_ascii=False)
            break

        # 将 Pydantic 对象转为 dict
        messages.append(msg.model_dump(exclude_unset=True))
        for tool_call in msg.tool_calls:
            func_name = ""
            arguments = {}
            try:
                arguments = json.loads(tool_call.function.arguments)
                func_name = tool_call.function.name
            except Exception as e:
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"参数解析错误: {e}"})
                continue

            if func_name in ["add_event", "delete_event", "list_events"]:
                arguments["_tenant"] = memory.get_tenant(session_id)

            if role == "viewer" and func_name in ["web_search", "fetch_webpage"]:
                result = "无权限执行此操作。"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue

            if func_name in ["aggregate", "generate_chart"] and current_temp_file and not arguments.get("file_name"):
                arguments["file_name"] = os.path.basename(current_temp_file)

            yield json.dumps({"type": "tool", "content": f"正在调用工具: {func_name}，请稍候..."}, ensure_ascii=False)
            await asyncio.sleep(0.1) # 给前端一点渲染时间

            result = None
            t_tool = time.time()
            for retry in range(MAX_RETRIES + 1):
                try:
                    if func_name in AVAILABLE_TOOLS:
                        # 【核心修复】将同步工具调用放入线程池中执行，避免阻塞异步事件循环
                        result = await asyncio.to_thread(AVAILABLE_TOOLS[func_name], **arguments)
                    else:
                        result = f"未找到工具 {func_name}"
                    break
                except Exception as e:
                    if retry == MAX_RETRIES:
                        result = f"工具执行错误（已重试 {MAX_RETRIES} 次）: {e}"
                    else:
                        # 异步环境下的重试等待需使用 asyncio.sleep
                        await asyncio.sleep(0.5)

            # 图片通道分离
            if func_name == "generate_chart" and result:
                img_match = re.search(r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)', str(result))
                if img_match:
                    full = img_match.group(0)
                    image_output = full[full.index('](') + 2 : -1]
                    result = re.sub(r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)', '[图片已就绪]', str(result))

            if func_name == "search_knowledge" and result:
                ids_match = re.search(r'\[RETRIEVED_IDS\](.*?)\[/RETRIEVED_IDS\]', str(result))
                if ids_match:
                    for doc_id in ids_match.group(1).split(','):
                        doc_id = doc_id.strip()
                        if doc_id and doc_id not in collected_sources:
                            collected_sources.append(doc_id)
                else:
                    for m in _extract_ids_from_text(str(result)):
                        if m not in collected_sources:
                            collected_sources.append(m)

            # 【核心修复】补上遗漏的工具追踪记录，解决 tools=[] 的误报
            tool_trace.append({
                "iteration": iteration, "stage": "tool",
                "name": func_name, "cost_seconds": round(time.time() - t_tool, 3),
                "result_len": len(str(result)),
            })

            simple_log_tool(session_id, original_query, func_name, arguments, result)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # ⑥ 输出与后处理
    answer = output_guard(answer)

    # 1. 彻底清洗 LLM 写的所有无效图片标签
    answer = re.sub(r'!\[.*?\]\(.*?\)', '', answer)  # 清除 ![标题](url) 形式
    answer = re.sub(r'!\[.*?\]', '', answer)         # 清除 ![标题] 形式

    # 2. 前缀清洗，去除 LLM 可能误带的旧前缀
    answer = re.sub(r'^(根据|基于).*?数据，|根据数据文件[^\n]*?，', '', answer).strip()

    # 3. 统一数据来源前缀
    if source_prefix and answer.startswith(source_prefix):
        answer = answer[len(source_prefix):].strip()
    # 【核心修复】强制拼接换行，确保 # 标题独占一行，正则表达式能正确匹配
    if source_prefix and answer.startswith("#"):
        answer = source_prefix + "\n\n" + answer
    else:
        answer = source_prefix + answer

    # 4. 图片精准插入（支持饼图标题）
    if image_output:
        img_md = f"![图表]({image_output})"
        chart_title_match = re.search(r'^(#{1,3}\s+.*?(柱状图|饼图|图表|趋势图).*?)$', answer, re.MULTILINE)
        if chart_title_match:
            pos = chart_title_match.end()
            answer = answer[:pos] + "\n\n" + img_md + "\n" + answer[pos:]
        else:
            title_match = re.search(r'^(#{1,3}\s+.+)$', answer, re.MULTILINE)
            if title_match:
                pos = title_match.end()
                answer = answer[:pos] + "\n\n" + img_md + "\n" + answer[pos:]
            else:
                answer = answer.rstrip() + "\n\n" + img_md

    # ⑦ 后置记忆
    answer_for_memory = re.sub(
        r'\n*\s*>?\s*说明：本[次轮].{0,20}资料中[^\n]*(?:\n(?!\n|如需|如果您)[^\n]*)*',
        '', answer
    ).strip()
    if not answer_for_memory:
        answer_for_memory = answer
    memory.append(session_id, history_text, answer_for_memory)

    llm_count = len([t for t in tool_trace if t['stage'] == 'llm'])
    tool_names = [t['name'] for t in tool_trace if t['stage'] == 'tool']
    print(f"###trace### session={session_id}, llm_calls={llm_count}, tools={tool_names}")

    # 打字机效果
    chunk_size = 2
    for i in range(0, len(answer), chunk_size):
        chunk = answer[i:i+chunk_size]
        context_data = collected_sources if i == 0 else []
        yield json.dumps({"type": "answer", "content": chunk, "contexts": context_data}, ensure_ascii=False)
        await asyncio.sleep(0.01)

# ================= API 接口 =================
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
            return {"answer": "【系统提示】您的账号已被禁用。", "image": ""}

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
        # 发生异常时，也要返回 SSE 格式，保证前端流式解析不崩溃
        async def error_stream():
            yield f"data: {json.dumps({'type': 'answer', 'content': f'系统处理异常: {e}'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")


@app.post("/api/upload_temp")
async def api_upload_temp(file: UploadFile = File(...), session_id: str = Form(...)):
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
    safe_name = os.path.basename(file_name)
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.exists(file_path):
        return {"status": "error", "message": "文件不存在"}
    return FileResponse(path=file_path, filename=safe_name, media_type='application/octet-stream')


@app.get("/api/users/list")
async def api_users_list():
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("SELECT username, real_name, role, department, contact, status FROM users")
    users = [{"username": r[0], "real_name": r[1], "role": r[2], "department": r[3], "contact": r[4], "status": r[5]} for r in cursor.fetchall()]
    conn.close()
    return {"status": "success", "data": users}


@app.post("/api/users/add")
async def api_users_add(username: str = Form(...), pin: str = Form(...), real_name: str = Form(""), role: str = Form("viewer"), department: str = Form(""), contact: str = Form(""), status: str = Form("正常")):
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
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE username = ?", (username,))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户已删除"}


@app.post("/api/users/update")
async def api_users_update(username: str = Form(...), role: str = Form(...), status: str = Form("正常")):
    conn = sqlite3.connect(os.path.join(UPLOAD_DIR, "users.db"))
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = ?, status = ? WHERE username = ?", (role, status, username))
    conn.commit()
    conn.close()
    return {"status": "success", "message": "用户更新成功"}


@app.get("/api/health")
async def api_health():
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
async def api_logs():
    plan_log_path = os.path.join(UPLOAD_DIR, "plan_log.json")
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
    plan_log_path = os.path.join(UPLOAD_DIR, "plan_log.json")
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


@app.post("/api/history/clear")
async def api_history_clear(session_id: str = Form(...)):
    """【故事 6.3】清空指定 session 的所有历史消息"""
    try:
        memory.clear(session_id)
        print(f"###历史清空### session={session_id}")
        return {"status": "success", "message": "已清空会话历史"}
    except Exception as e:
        print(f"###历史清空### 失败: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/api/feedback")
async def api_feedback(session_id: str = Form(...), feedback_type: str = Form(...), feedback_text: str = Form("")):
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

if os.path.exists(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")

    @app.get("/")
    async def serve_react():
        return FileResponse(os.path.join(DIST_DIR, "index.html"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api"):
            return {"detail": "Not Found"}
        file_path = os.path.join(DIST_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(DIST_DIR, "index.html"))