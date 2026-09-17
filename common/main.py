# common/main.py

"""
common/main.py - 核心聊天逻辑与 API 路由

================================================================================
RAG 架构组件映射（标准 RAG Architecture）
================================================================================

本系统实现了标准 RAG 架构的四个主要组件，组件位置对照如下：

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
        · 向量路：ChromaDB (HNSW)    语义相似
        · 关键词路：BM25 + jieba      精确匹配
        · 融合：RRF (Reciprocal Rank Fusion)
      - common/main.py::_retrieve_background    物理层检索封装

【3. 集成层 Integration Layer】
    职责：协调 RAG 的整体功能
    位置：
      - common/main.py::_route_query           语义路由（路由器/编排器）
        · 判断问题类型：knowledge/data/schedule/realtime/chitchat
        · 决定是否触发检索
      - common/main.py::_build_schema_hint     数据文件 schema 注入
      - common/main.py::chat_core              主流程集成
        · 前置管线 → 路由 → 检索/schema 注入 → LLM 决策 → 工具执行 → 后置管线

【4. 生成器 Generator】
    职责：根据用户查询和检索数据创建输出
    位置：
      - common/main.py::client                  DeepSeek LLM 客户端
      - common/main.py::SYSTEM_PROMPT           LLM 能力边界定义
      - common/main.py::chat_core 的工具执行循环   LLM 决策 → 工具 → 结果

【其他组件】
  · 排名器 Ranker：RRF 融合 + 阈值筛选（rag_v2.py::search_knowledge_v2）
  · 输出处理 Output Handler：output_guard + 物理层前缀（main.py::chat_core）

================================================================================
架构原则（AI 原生）
================================================================================
- 决策层：LLM 基于语义自主决策调什么工具
- 执行层：工具执行 + 重试 + 审计日志
- 确定性物理层：数据来源前缀、图片 URL、检索 ID 提取不依赖 LLM
- 路由器层：通过语义路由决定"是否检索"，而非"每次提问都检索"

================================================================================
模块职责
================================================================================
1. FastAPI 应用与 API 路由（/api/chat /api/login /api/kb/* /api/users/*）
2. chat_core：核心对话主流程
3. 语义路由层：_route_query
4. RAG 检索层：_retrieve_background / _build_schema_hint
5. 前置管线：guardrails / 权限 / 临时文件 / pending 工具
6. 后置管线：输出校验 / 物理层前缀 / 记忆存储
"""

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

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
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
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
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

def _cleanup_old_charts(days: int = 30):
    """
    【故事 4】清理 uploads/charts/ 目录下超过指定天数的旧图表。

    策略：
    - 只清理 .png 文件（图表专用）
    - 保留其它文件（如非图表文件，避免误删）
    - 每次启动时执行一次，不引入定时任务

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
    _cleanup_old_charts(days=30)  # 【故事 4】启动时清理超过 30 天的旧图表
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


# ================= V3 系统提示（语义边界，不规定流程） =================
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

【输出风格】
- 你的回答应当有洞察力、自然流畅。将工具返回的原始数据转化为易于理解的趋势描述，鼓励使用 Markdown 表格美化数据。
- 严禁直接把原始 JSON 或文本格式的工具返回结果直接粘贴给用户！你必须对数据进行提炼和总结。

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
        with open("plan_log.json", "a", encoding="utf-8") as f:
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

    这是 RAG 架构的"路由器层"——决定是否查询外部数据，
    还是直接交给 LLM 处理。避免"每次提问都检索"的过度行为。

    返回 5 种类别：
    - knowledge: 企业知识库类（触发背景检索）
    - data:      数据文件类（触发 schema 注入）
    - schedule:  日程类（不检索）
    - realtime:  实时信息类（不检索）
    - chitchat:  闲聊类（不检索）

    原理：用轻量 LLM 做语义判断，不使用关键词匹配。
    成本：每次约 0.5-1s 的额外 LLM 调用（可接受）。
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
    去样本化：只告诉 LLM 有什么文件、有哪些列，不提供具体数值样本。
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(base_dir, "rag_data.json")

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
    与 LLM 决策解耦：无论 LLM 是否调用 search_knowledge，本函数总会执行。

    返回：
        {"text": 拼接后的上下文, "ids": 结构化 ID 列表}
    """
    from common.rag_v2 import search_knowledge_v2
    try:
        result = search_knowledge_v2(query, "")
        if isinstance(result, dict):
            text = result.get("context_text", "")
            sources = result.get("sources", [])
            ids = [s.get("doc_id") for s in sources if s.get("doc_id")]
            # 背景资料截断，避免撑爆 system prompt
            if text and len(text) > 8000:
                text = text[:8000] + "\n...（背景资料过长，已截断）"
            return {"text": text, "ids": ids}
    except Exception as e:
        print(f"###背景检索### 失败: {e}")
    return {"text": "", "ids": []}

async def chat_core(session_id: str, query: str, user_text: str = None,
    query_worker=None, command_worker=None, TOOL_ROUTER=None,
    image_base64: str = None, temp_file_path: str = None):
    """
    V3 架构：schema 事实注入 + LLM 自主决策 + 确定性执行
    """
    # ① 前置管道
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)
    if user_info and user_info.get("status") == "禁用":
        return "【系统安全提示】您的账号已被管理员禁用。", []

    original_query = query
    history_text = user_text if user_text else original_query

    # 提前初始化，防止作用域报错
    tool_trace = []
    source_prefix = ""

    is_safe, err_msg = input_guard(query)
    if not is_safe:
        return err_msg, []

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
        return output_guard(result), []

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
        return output_guard(answer), []

    # 记忆装载
    history = memory.get(session_id)[-20:]

    # ② 语义路由（RAG 路由器层）
    #    - 判断问题类型，决定是否执行检索 / 注入 schema
    #    - 这是 AI 原生架构的"路由器/编排器"，避免一刀切
    route = await _route_query(query)
    system_content = SYSTEM_PROMPT

    # 【数据类】注入 schema
    if route == "data":
        schema_hint = _build_schema_hint()
        if schema_hint:
            system_content = SYSTEM_PROMPT + "\n\n" + schema_hint
            print(f"###schema### 注入 {len(schema_hint)} 字符的数据 schema")
        else:
            print(f"###schema### 无可用 schema，跳过")
    else:
        print(f"###schema### 跳过（路由={route}）")

    # 【物理层数据来源】前缀按"实际数据来源"动态生成，不能硬编码
    #   路由已经判断了问题类型，前缀应与之对应：
    #   - knowledge → 企业知识库
    #   - data      → 数据文件分析
    #   - realtime  → 实时信息
    #   - schedule  → 不加前缀（直接显示日程）
    #   - chitchat  → 不加前缀（闲聊）
    #   - 上传文件  → 根据上传文件
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
    elif route == "schedule":
        source_prefix = ""
    else:  # chitchat
        source_prefix = ""

    # 【知识类背景检索】只有 knowledge 类问题才执行检索
    #   - 这是 RAG 架构的"检索器层"，由路由器决定是否触发
    #   - 检索结果注入 system prompt，作为 LLM 回答的背景资料
    bg = {"text": "", "ids": []}
    if route == "knowledge":
        bg = _retrieve_background(query)
        if bg["text"]:
            system_content += (
                f"\n\n【企业知识库背景资料】\n{bg['text']}\n\n"
                "【背景资料说明】以上是系统自动检索到的企业知识库内容。"
                "请优先基于这些资料回答用户问题。"
                "如果资料与问题无关，请忽略，不要强行引用。"
            )
            print(f"###背景检索### 命中 {len(bg['ids'])} 个 ID: {bg['ids']}")
        else:
            print(f"###背景检索### 无相关命中")
    else:
        print(f"###背景检索### 跳过（路由={route}）")

    system_content += "\n\n【回答要求】请不要在回答开头写任何关于数据来源的说明。直接以'以下是...'开头。系统会自动为你添加前缀。"
    system_content += "\n【图表规则】禁止在回答中手动输出任何图片 Markdown（例如 ![图表](url) 或 ![描述]），系统会自动在正确位置插入图表。你只需负责撰写数据表格和文字分析。"

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
    collected_sources = list(bg["ids"])  # 从背景检索结果初始化

    MAX_ITERATIONS = 8
    MAX_RETRIES = 2

    # ⑤ LLM 决策 + 工具执行循环
    for iteration in range(MAX_ITERATIONS):
        t_llm = time.time()
        # 【防御性重试】LLM 调用失败时最多重试 2 次，间隔 1s
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
                # 【空值防御】API 偶尔返回 None，视为失败触发重试
                if response is None or not getattr(response, "choices", None):
                    last_error = "API 返回空响应"
                    print(f"###LLM重试### 第 {llm_retry+1} 次：API 返回 None，1s 后重试")
                    time.sleep(1)
                    continue
                break  # 成功，跳出重试循环
            except Exception as e:
                last_error = e
                if llm_retry < 2:
                    print(f"###LLM重试### 第 {llm_retry+1} 次失败: {type(e).__name__}: {e}，1s 后重试")
                    time.sleep(1)

        # 3 次重试后仍失败 → 返回错误
        if response is None or not getattr(response, "choices", None):
            answer = f"模型调用失败（已重试 3 次）: {last_error}"
            memory.append(session_id, original_query, answer)
            tool_trace.append({"iteration": iteration, "stage": "llm", "error": str(last_error)})
            return output_guard(answer), collected_sources

        t_llm_cost = round(time.time() - t_llm, 3)
        msg = response.choices[0].message
        tool_trace.append({
            "iteration": iteration, "stage": "llm",
            "cost_seconds": t_llm_cost,
            "has_tool_calls": bool(msg.tool_calls),
        })

        if not msg.tool_calls:
            answer = msg.content

            # 【故事11修复】决策校验器：数据意图命中但未调工具 → 通用反思
            # 【意图词只是"触发反思"的信号，不是"决定调用哪个工具"的规则】
            # 最终调什么工具，完全由 LLM 基于工具描述自主决策
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
                print(f"###决策校验### 意图词命中（data={has_data_intent}, knowledge={has_knowledge_intent}）但 tools=[]，触发反思重试")
                messages.append({
                    "role": "system",
                    "content": (
                        "你刚才的回答没有调用任何工具。请重新审视用户的问题，"
                        "判断是否需要调用工具。可用工具及其适用场景见工具描述，"
                        "请根据用户意图自主选择最合适的工具。"
                    )
                })
                continue

            break

        # 将 Pydantic 对象转为 dict，保持 messages 列表类型一致
        messages.append(msg.model_dump(exclude_unset=True))
        for tool_call in msg.tool_calls:
            func_name = ""
            arguments = {}
            try:
                arguments = json.loads(tool_call.function.arguments)
                func_name = tool_call.function.name
            except Exception as e:
                messages.append({"role": "tool", "tool_call_id": tool_call.id,
                                 "content": f"参数解析错误: {e}"})
                continue

            if func_name in ["add_event", "delete_event", "list_events"]:
                arguments["_tenant"] = memory.get_tenant(session_id)

            if role == "viewer" and func_name in ["web_search", "fetch_webpage"]:
                result = "无权限执行此操作。"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                continue

            # 临时文件模式下，如果 LLM 传的 file_name 为空，用临时文件名补
            if func_name in ["aggregate", "generate_chart"] and current_temp_file and not arguments.get("file_name"):
                arguments["file_name"] = os.path.basename(current_temp_file)
                print(f"[Tool] {func_name} 注入 file_name（临时文件）: {arguments['file_name']}")

            result = None
            t_tool = time.time()
            for retry in range(MAX_RETRIES + 1):
                try:
                    if func_name in AVAILABLE_TOOLS:
                        result = AVAILABLE_TOOLS[func_name](**arguments)
                    else:
                        result = f"未找到工具 {func_name}"
                    break
                except Exception as e:
                    if retry == MAX_RETRIES:
                        result = f"工具执行错误（已重试 {MAX_RETRIES} 次）: {e}"
                    else:
                        print(f"[Tool] {func_name} 第 {retry+1} 次失败: {e}")
                        time.sleep(0.5)
            t_tool_cost = round(time.time() - t_tool, 3)

            # 【图片通道分离】generate_chart 返回的 URL 图片不经过 LLM，由后端直接拼接
            if func_name == "generate_chart" and result:
                img_match = re.search(
                    r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)',
                    str(result)
                )
                if img_match:
                    full = img_match.group(0)
                    # 从 ![alt](/charts/xxx.png) 中提取纯 URL
                    image_output = full[full.index('](') + 2 : -1]
                    print(f"###图片通道### 已捕获 generate_chart 图片 URL: {image_output}")
                    # 给 LLM 的 tool 结果中，把图片 markdown 替换成占位提示，让 LLM 不再复制
                    result = re.sub(
                        r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)',
                        '[图片已就绪]',
                        str(result)
                    )
            if func_name == "search_knowledge" and result:
                # 【诊断增强】优先从结构化标记 [RETRIEVED_IDS] 提取真实检索 ID
                ids_match = re.search(r'\[RETRIEVED_IDS\](.*?)\[/RETRIEVED_IDS\]', str(result))
                if ids_match:
                    for doc_id in ids_match.group(1).split(','):
                        doc_id = doc_id.strip()
                        if doc_id and doc_id not in collected_sources:
                            collected_sources.append(doc_id)
                    print(f"###contexts来源### 结构化提取 | 命中 {len(collected_sources)} 个: {collected_sources}")
                else:
                    # 保底：旧的文本提取逻辑
                    for m in _extract_ids_from_text(str(result)):
                        if m not in collected_sources:
                            collected_sources.append(m)
                    if collected_sources:
                        print(f"###contexts来源### 文本兜底提取 | 命中 {len(collected_sources)} 个: {collected_sources}")
                    else:
                        print(f"###contexts来源### 检索无命中 | contexts=[]")

            tool_trace.append({
                "iteration": iteration, "stage": "tool",
                "name": func_name, "cost_seconds": t_tool_cost,
                "result_len": len(str(result)),
            })

            simple_log_tool(session_id, original_query, func_name, arguments, result)

            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # ⑥ 输出
    answer = output_guard(answer)

    # 物理层拼接前缀，去除 LLM 可能误带的旧前缀
    answer = re.sub(r'^(根据|基于).*?数据，', '', answer).strip()

    # 【位置修复】将图片 markdown 直接插入到第一个 markdown 标题下方
    # 注意：必须在 memory.append 之前执行，否则 memory 里存的是不含图片的旧版
    # 【位置修复】优先插入到包含"📊"或"汇总"的标题下方
    # 【彻底清洗】清除 LLM 写的所有图片标签（包括残缺的），防止标签裸露
    answer = re.sub(r'!\[.*?\](\s*\(.*?\))?', '', answer)

    answer = source_prefix + answer

    # 【位置修复】优先插入到包含"📊"或"汇总"的标题下方
    if image_output:
        img_md = f"![图表]({image_output})"
        # 尝试寻找带有 📊 或 汇总 的标题行
        chart_title_match = re.search(r'^(#{1,3}\s+.*?(📊|汇总|图表).*?)$', answer, re.MULTILINE)
        if chart_title_match:
            pos = chart_title_match.end()
            answer = answer[:pos] + "\n\n" + img_md + "\n" + answer[pos:]
        else:
            # 如果没有匹配到，退回到第一个标题下方
            title_match = re.search(r'^(#{1,3}\s+.+)$', answer, re.MULTILINE)
            if title_match:
                pos = title_match.end()
                answer = answer[:pos] + "\n\n" + img_md + "\n" + answer[pos:]
            else:
                answer = answer.rstrip() + "\n\n" + img_md

    # ⑦ 后置管道（记忆清洁）—— 此时 answer 已含图片 markdown
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

    return answer, collected_sources


async def generate_plan(user_query, history, client):
    return None


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
        result = await chat_core(
            request.session_id, request.query, request.user_text,
            _query_worker, _command_worker, _tool_router,
            temp_file_path=getattr(request, 'temp_file_path', None)
        )
        if result is None:
            return {"answer": "系统处理异常：内部返回空。", "contexts": [], "image": ""}
        answer, retrieved_ids = result
        return {
            "answer": answer,
            "contexts": retrieved_ids,
        }
    except Exception as e:
        import traceback
        print(f"###严重Bug### {traceback.format_exc()}")
        return {"answer": f"系统处理异常: {e}", "image": ""}


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
        print(f"[Temp] 会话 {session_id} 上传临时文件: {file_path}")
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
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rag_file = os.path.join(BASE_DIR, "rag_data.json")
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
        if "UNIQUE constraint failed" in str(e):
            return {"status": "error", "message": "用户已存在"}
        return {"status": "error", "message": f"添加失败: {e}"}


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
        return FileResponse(os.path.join(DIST_DIR, "index.html"))