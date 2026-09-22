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

模块职责：
1. FastAPI 路由定义（api_chat / api_login / ...）
2. chat_core_stream：LLM 决策 + 工具执行 + 输出管线
3. _retrieve_background：物理层无条件知识库检索
4. _route_query：语义路由（判断问题类型）
5. _build_schema_hint：从 rag_data.json 构建数据文件 schema
6. SYSTEM_PROMPT：LLM 能力边界定义

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
    """初始化示例 SQLite 数据库（sample.db）"""
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


def write_log_to_db(entry):
    """将工具调用审计日志写入 SQLite 数据库"""
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


# ================= V3 系统提示 =================
SYSTEM_PROMPT = """
你是一个企业级AI智能助手。你拥有工具调用能力，请根据用户意图自主决策调用哪些工具。

【最高优先级：回答边界与拒答规则（绝对禁令）】
1. **必须且只能基于【企业知识库背景资料】和工具返回的真实数据回答**，严禁使用模型自身常识、经验或推测补充任何未在资料中出现的内容。
2. **若【企业知识库背景资料】为空，或资料与用户问题完全无关**（如财务报销、发票开具、保修期查询、行政政策等知识库未覆盖的领域），**必须直接回答：**
   「根据企业知识库文档，未能找到关于该问题的具体说明，建议咨询相关部门。」
   **严禁编造、严禁套用无关资料强行作答。**
3. **若背景资料中包含与用户问题症状相近、机理相似的文档**（例如用户描述"系统升级后打印中断"，而资料中是"重装系统后打印机失踪"，二者都属于系统变动导致打印异常），**必须基于该相近文档给出处理建议**，并在回答中说明"这与 [XX-XX] 描述的场景相近，可参考其处理方式"。**不得因字面措辞不同就拒答。**
4. **回答中必须引用命中的文档编号**（如 [IT-01]、[TS-06]）。

【能力边界】
- **系统已自动检索企业知识库，背景资料已注入 system prompt**。请优先基于背景资料回答。
- 如果背景资料不足以回答，可调用 search_knowledge 做**进一步**检索。
- 涉及数据文件的统计、趋势、聚合、筛选 → 使用 aggregate
- 涉及日程 → 使用 list_events / add_event / delete_event
- 涉及实时信息 → 使用 web_search
- 涉及文件概况 → 使用 analyze_file
- 涉及图表可视化（折线图/柱状图/饼图等）→ 使用 generate_chart
- 涉及企业内部员工/薪资的SQL查询 → 使用 query_database

【内置数据库说明】
系统内置了一个 SQLite 数据库（sample.db），其中包含一个 employees 表。
表结构：id (INTEGER), name (TEXT), position (TEXT), salary (INTEGER)。
当用户询问员工信息、工资、薪资总和、最高/最低薪资时，请直接使用 query_database 工具执行 SELECT 语句来获取真实数据。

如果知识库和工具都无法解决，请坦诚告知用户。

【图表生成绝对规则（最高优先级，违反将导致系统错误）】
- 如果用户提问中明确包含“饼图”，调用 generate_chart 时，chart_type 参数**必须填写 "pie"**，严禁填入 "line" 或 "bar"。
- 如果用户提问中明确包含“柱状图”，chart_type 参数**必须填写 "bar"**。
- 如果用户提问中明确包含“折线图”，chart_type 参数**必须填写 "line"**。
- 如果用户只提了“趋势”，没有明确图表类型，才可以使用 "line"。

【few-shot 参考】（仅展示逻辑，严禁照抄示例中的字符串）
用户问："将趋势绘制成饼图"
思考：用户明确要求饼图，chart_type 必须填 "pie"。
调用：generate_chart(file_name="<实际文件>", filter_json="{}", agg_column="<实际金额列>", agg_func="sum", group_by="month", chart_type="pie", title="<根据上下文生成的标题>")

【输出风格与结构模板】
为了确保最佳的可视化报告体验，你的回答必须严格遵守以下结构（使用 Markdown）：
1. 首先输出一级标题：`# 各月咖啡销售趋势柱状图`（或类似包含“柱状图/饼图/图表”的标题）
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
    """判断工具执行结果是否包含错误标识"""
    return ("错误" in str(result)) or ("失败" in str(result))


def simple_log_tool(session_id, user_query, tool_name, arguments, result):
    """
    记录工具调用审计日志到本地文件（plan_log.json）和 SQLite 数据库
    """
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
    """从工具返回的文本中提取结构化文档 ID（如 IT-01, TS-02）"""
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

    返回类别：
    - knowledge: 企业知识库类（触发背景检索）
    - data: 数据文件类（触发 schema 注入）
    - schedule: 日程类
    - realtime: 实时信息类
    - chitchat: 闲聊类

    原理：
    用轻量 LLM 调用做语义判断，不使用关键词匹配。
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
                        "- knowledge: 企业内部的故障排查、IT 支持、文档查询、FAQ、流程规定、行政政策\n"
                        "- data: 对数据文件（CSV/Excel）进行统计、查询、聚合、可视化\n"
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
def _build_schema_hint(query: str = "") -> str:
    """
    【V3 核心】基于向量语义匹配，挑选 Top 3 最相关文件的 schema 注入。
    【核心修复】只匹配结构化数据文件（含 schema），过滤掉 Markdown、PDF 等文本文件。
    """
    import numpy as np
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

    all_files = store.get("files", [])
    # 【核心修复】只过滤出含 schema 的结构化数据文件，排除 PDF/MD 文档
    structured_files = [f for f in all_files if f.get("schema")]
    if not structured_files:
        return ""

    matched_files = []
    try:
        from common.rag_v2 import _vector_model
        if _vector_model and query:
            # 1. 构建每个结构化文件的文本描述（包含文件名、标签、列名）
            file_descriptions = []
            for item in structured_files:
                fname = item.get("file_name", "")
                tags = item.get("tags", "")
                schema = item.get("schema", {})
                col_names = [c.get("name", "") for c in schema.get("columns", [])]
                desc = f"文件：{fname}，标签：{tags}，包含列：{','.join(col_names)}"
                file_descriptions.append(desc)

            # 2. 计算 Query 与文件描述的语义相似度
            query_emb = _vector_model.encode([query], normalize_embeddings=True)
            file_embs = _vector_model.encode(file_descriptions, normalize_embeddings=True)
            similarities = np.dot(file_embs, query_emb.T).flatten()

            # 3. 排序并匹配，使用 0.3 的合理阈值
            top_indices = np.argsort(similarities)[::-1]
            for idx in top_indices[:3]:
                score = similarities[idx]
                if score > 0.3:
                    matched_files.append(structured_files[idx])
                    print(f"###schema### 向量匹配文件: {structured_files[idx]['file_name']}，相似度: {score:.4f}")
        else:
            matched_files = structured_files[:3]
    except Exception as e:
        print(f"###schema### 向量匹配异常，降级为前 3 个结构化文件: {e}")
        matched_files = structured_files[:3]

    files_to_inject = matched_files[:3] if matched_files else structured_files[:3]

    lines = ["【可用数据文件】", "⚠️ 注意：以下仅为文件概况。你必须根据用户提问，从这些文件中选择最合适的一个来调用工具。"]
    for item in files_to_inject:
        fname = item.get("file_name", "")
        schema = item.get("schema")
        if not schema:
            continue
        lines.append(f"\n📄 {fname}（{schema.get('row_count', '?')} 行）")
        for col in schema.get("columns", []):
            col_name = col.get("name")
            col_type = col.get("type")
            type_hint = {"date": "日期", "numeric": "数值", "category": "分类", "text": "文本"}.get(col_type, col_type)
            lines.append(f"  - {col_name}（{type_hint}）")

    print(f"###schema### 动态构建成功，最终注入文件数: {len(files_to_inject)}")
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

async def chat_core_stream(session_id: str, query: str, user_text: str = None,
    query_worker=None, command_worker=None, TOOL_ROUTER=None,
    image_base64: str = None, temp_file_path: str = None):
    """
    V3 架构（流式版）：schema 事实注入 + LLM 自主决策 + 确定性执行

    输入：
        session_id: 会话ID（包含用户名和窗口名）
        query: 用户当前提问
        user_text: 用户原始输入（用于记忆）
        temp_file_path: 临时上传文件的路径（可选）
    """
    # ① 前置管道：用户状态校验与安全过滤
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)
    
    # 【核心修复】恢复用户不存在/已被禁用的安全拦截逻辑
    if not user_info:
        print(f"###系统安全### 用户 {real_username} 不存在，拒绝访问")
        yield json.dumps({"type": "answer", "content": "【系统安全提示】用户不存在。"}, ensure_ascii=False)
        return

    if user_info.get("status") == "禁用":
        print(f"###系统安全### 用户 {real_username} 已被禁用，拒绝访问")
        yield json.dumps({"type": "answer", "content": "【系统安全提示】您的账号已被管理员禁用。"}, ensure_ascii=False)
        return

    original_query = query
    history_text = user_text if user_text else original_query

    tool_trace = []
    source_prefix = ""

    # 【安全过滤】输入安全检查
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        yield json.dumps({"type": "status", "content": err_msg}, ensure_ascii=False)
        return

    # 启动 Worker 循环（确保 QueryWorker / CommandWorker 处于运行状态）
    if not query_worker.is_running:
        asyncio.create_task(query_worker.run_loop())
        query_worker.is_running = True
    if not command_worker.is_running:
        asyncio.create_task(command_worker.run_loop())
        command_worker.is_running = True

    # 【工具确认机制】若存在待确认工具且用户输入“确认”，则直接执行
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

    # 【临时文件处理】挂载当前会话关联的临时文件，支持多轮对话携带文件
    if temp_file_path:
        _session_temp_files[session_id] = temp_file_path
        current_temp_file = temp_file_path
    else:
        _session_temp_files.pop(session_id, None)
        current_temp_file = None

    # 【时间快路径】高频问题无需走 RAG，直接返回
    if any(kw in query for kw in ["现在几点", "现在时间", "几点了", "什么时间", "当前时间"]):
        time_result = get_current_time()
        answer = f"现在是 {time_result}（北京时间）。"
        simple_log_tool(session_id, original_query, "get_current_time", {}, time_result)
        memory.append(session_id, history_text, answer)
        yield json.dumps({"type": "answer", "content": output_guard(answer), "contexts": []}, ensure_ascii=False)
        return

    # 记忆装载：获取最近 20 轮对话历史
    history = memory.get(session_id)[-20:]

    # ② 语义路由层：判断问题类型
    yield json.dumps({"type": "status", "content": "正在分析问题类型..."}, ensure_ascii=False)
    route = await _route_query(query)
    system_content = SYSTEM_PROMPT

    # 数据类问题注入数据文件的 schema 提示
    if route == "data":
        schema_hint = _build_schema_hint(query)
        if schema_hint:
            system_content = SYSTEM_PROMPT + "\n\n" + schema_hint
            print(f"###schema### 注入 {len(schema_hint)} 字符的数据 schema")
        else:
            print(f"###schema### 无可用 schema，跳过")
    else:
        print(f"###schema### 跳过（路由={route}）")

    # 【物理层数据来源前缀】根据实际数据来源生成前缀，保证最终输出对用户透明
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

    # 【知识类背景检索】仅当路由为 knowledge 时才触发
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
    
    # 【系统时间注入】防止 LLM 编造日期
    current_date_str = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日 %H:%M:%S")
    system_content += f"\n\n【系统时间信息】当前系统时间是 {current_date_str}。请以此时间为准，不要依赖你的内部知识库或历史记忆。"

    # ③ 角色权限过滤：基于 RBAC 过滤工具
    role = user_info.get("role", "viewer") if user_info else "viewer"
    if role not in ROLE_PERMISSIONS:
        role = "manager"

    allowed_tools = TOOLS_METADATA
    # 【核心修复】不删除工具，保留其定义，让 LLM 尝试调用，从而触发物理层拦截逻辑。
    # 之前因为删除了工具，导致 LLM 没有工具可用，只能长篇大论地解释自己没有联网能力。
    # if role == "viewer":
    #     allowed_tools = [t for t in TOOLS_METADATA
    #                      if t["function"]["name"] not in ["web_search", "fetch_webpage"]]

    # ④ 构建 messages（实时类问题跳过历史记录，防止历史污染）
    messages = [{"role": "system", "content": system_content}]
    if route != "realtime":
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
        
        # 【防御性重试】LLM 调用失败时最多重试 2 次，间隔 1s
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
                traceback.print_exc()
                if llm_retry < 2:
                    time.sleep(1)

        if response is None or not getattr(response, "choices", None):
            answer = f"模型调用失败（已重试 3 次）: {last_error}"
            print(f"###严重Bug### 模型调用彻底失败: {last_error}")
            memory.append(session_id, original_query, answer)
            yield json.dumps({"type": "answer", "content": output_guard(answer), "contexts": collected_sources}, ensure_ascii=False)
            return

        msg = response.choices[0].message

        # 【核心修复】将 trace 记录提前到 LLM 响应后，无论是否调用工具都记录
        t_llm_cost = round(time.time() - t_llm, 3)
        tool_trace.append({
            "iteration": iteration, "stage": "llm",
            "cost_seconds": t_llm_cost,
            "has_tool_calls": bool(msg.tool_calls),
        })

        if not msg.tool_calls:
            answer = msg.content
            # 容错：如果模型输出空内容，触发重试
            if not answer or not answer.strip():
                print(f"###警告### LLM 返回了空内容，触发重试")
                if iteration < MAX_ITERATIONS - 1:
                    continue
                answer = "抱歉，模型未能生成有效回答，请稍后重试。"

            # 【决策校验器】数据意图命中但未调工具时，触发反思重试
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
                    "content": "你刚才的回答没有调用任何工具。请重新审视用户的问题，判断是否需要调用工具。可用工具及其适用场景见工具描述，请根据用户意图自主选择最合适的工具。"
                })
                continue

            yield json.dumps({"type": "status", "content": "正在生成最终报告..."}, ensure_ascii=False)
            break

        # 将 Pydantic 对象转为 dict，并追加到 messages
        messages.append(msg.model_dump(exclude_unset=True))
        
        # 执行多个工具调用
        for tool_call in msg.tool_calls:
            func_name = ""
            arguments = {}
            try:
                arguments = json.loads(tool_call.function.arguments)
                func_name = tool_call.function.name
            except Exception as e:
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"参数解析错误: {e}"})
                continue

            # 【租户隔离】日程相关工具注入租户 ID
            if func_name in ["add_event", "delete_event", "list_events"]:
                arguments["_tenant"] = memory.get_tenant(session_id)

            # 【权限拦截】viewer 角色禁止调用联网工具
            # 【通用权限拦截】按 ROLE_PERMISSIONS 校验角色是否有权调用此工具
            # 不删除工具定义（否则 LLM 会编造理由），而是让 LLM 调用后物理层拒绝
            user_allowed_tools = ROLE_PERMISSIONS.get(role, [])
            has_permission = ("*" in user_allowed_tools) or (func_name in user_allowed_tools)
            # 额外收紧：viewer 无论权限列表怎么写，都不得联网
            if role == "viewer" and func_name in ["web_search", "fetch_webpage"]:
                has_permission = False

            if not has_permission:
                # 【物理层拦截】强制中断并返回不可逾越的提示，要求 LLM 不得解释
                result = f"【物理层安全拦截】当前账号（{role}）没有调用 {func_name} 的权限。请直接告知用户无权使用该功能，不要解释原因。"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                # 记录审计日志（可选，但推荐）
                simple_log_tool(session_id, original_query, func_name, arguments, "【权限拦截】")
                continue

            # 【临时文件注入】图表或数据聚合工具自动注入当前会话的临时文件
            if func_name in ["aggregate", "generate_chart"] and current_temp_file and not arguments.get("file_name"):
                arguments["file_name"] = os.path.basename(current_temp_file)
            
            # 【核心修复】将原始用户的提问传进参数中，供工具内部做“参数防呆纠正”
            if func_name == "generate_chart":
                arguments["_user_query"] = original_query

            yield json.dumps({"type": "tool", "content": f"正在调用工具: {func_name}，请稍候..."}, ensure_ascii=False)
            await asyncio.sleep(0.1)

            result = None
            t_tool = time.time()
            for retry in range(MAX_RETRIES + 1):
                try:
                    if func_name in AVAILABLE_TOOLS:
                        # 【核心修复】将工具执行路由到对应的 Worker 进行统计与缓存
                        # 如果 TOOL_ROUTER 可用，则使用 Worker，否则降级为直接线程执行
                        if TOOL_ROUTER and func_name in TOOL_ROUTER:
                            worker = TOOL_ROUTER[func_name]
                            task_result = await worker.send_task({
                                "tool": func_name,
                                "arguments": arguments
                            })
                            if "error" in task_result:
                                result = f"工具执行错误: {task_result['error']}"
                            else:
                                result = task_result.get("result", "")
                        else:
                            # 降级逻辑（防止 Worker 未初始化时崩溃）
                            result = await asyncio.to_thread(AVAILABLE_TOOLS[func_name], **arguments)
                    else:
                        result = f"未找到工具 {func_name}"
                    break
                except Exception as e:
                    if retry == MAX_RETRIES:
                        result = f"工具执行错误（已重试 {MAX_RETRIES} 次）: {e}"
                    else:
                        await asyncio.sleep(0.5)

            # 记录工具执行 trace
            tool_trace.append({
                "iteration": iteration, "stage": "tool",
                "name": func_name, "cost_seconds": round(time.time() - t_tool, 3),
                "result_len": len(str(result)),
            })

            # 【图片通道分离】generate_chart 返回的 URL 图片不经过 LLM，由后端直接拼接
            if func_name == "generate_chart" and result:
                img_match = re.search(r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)', str(result))
                if img_match:
                    full = img_match.group(0)
                    image_output = full[full.index('](') + 2 : -1]
                    result = re.sub(r'!\[.*?\]\(/charts/[a-f0-9]+\.png\)', '[图片已就绪]', str(result))

            # 【检索ID提取】提取结构化标记 [RETRIEVED_IDS] 中的真实 ID
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

            simple_log_tool(session_id, original_query, func_name, arguments, result)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(result)})
    else:
        answer = "抱歉，处理超时，请简化您的问题。"

    # ⑥ 输出与后处理
    answer = output_guard(answer)

    # 清理 LLM 写的无效图片标签
    answer = re.sub(r'!\[.*?\]\(.*?\)', '', answer)
    answer = re.sub(r'!\[.*?\]', '', answer)

    # 去除 LLM 可能误带的前缀
    answer = re.sub(r'^(根据|基于).*?数据，|根据数据文件[^\n]*?，', '', answer).strip()

    # 物理层拼接前缀，并去除 LLM 可能误带的旧前缀
    if source_prefix and answer.startswith(source_prefix):
        answer = answer[len(source_prefix):].strip()
    if source_prefix and answer.startswith("#"):
        answer = source_prefix + "\n\n" + answer
    else:
        answer = source_prefix + answer

    # 【图片插入】将图片精准插入到带有"柱状图/饼图/图表/趋势图"的标题下方
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

    # ⑦ 后置管道：记忆清洗与写入
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

    # 【打字机效果】将答案切成小块（每次2个字符）流式推送给前端
    chunk_size = 2
    for i in range(0, len(answer), chunk_size):
        chunk = answer[i:i+chunk_size]
        context_data = collected_sources if i == 0 else []
        yield json.dumps({"type": "answer", "content": chunk, "contexts": context_data}, ensure_ascii=False)
        await asyncio.sleep(0.01)


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
        async def error_stream():
            yield f"data: {json.dumps({'type': 'answer', 'content': f'系统处理异常: {e}'}, ensure_ascii=False)}\n\n"
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
    """获取操作日志列表（增加用户物理层权限校验）"""
    # 【物理层安全拦截】用户不允许查看系统日志，防止内部消息外泄
    if session_id:
        real_username = session_id.split('_')[0] if '_' in session_id else session_id
        user_info = get_user_info(real_username)
        if user_info and user_info.get("role") == "viewer":
            return {"status": "error", "message": "无权限查看系统日志，请联系管理员"}

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
async def api_logs_export():
    """导出操作日志为 CSV 文件"""
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