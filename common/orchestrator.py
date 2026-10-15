# common/orchestrator.py
"""
common/orchestrator.py - 主流程编排（US-13 模块化拆分）

职责：
1. 编排层：LLM 决策 + 工具执行循环
2. 语义分析、数据源发现、背景检索
3. 输出后处理与记忆写入

调用链：
    main.py::api_chat
      → main.py::chat_core_stream（薄转发）
        → orchestrator.py::run_orchestration（本文档）

拆分原则：
- 保持行为 100% 等价，不改任何业务逻辑
- 依赖方向单向：main → orchestrator
- 与外部模块（tools / guardrails / auth / memory）通过常规 import 协作

架构说明（AI Native 五层）：
- 编排层：本文件核心
- 数据与上下文层：通过 _retrieve_background 调用 common.rag_v2
- 模型层：通过 _client 调用 DeepSeek API
"""
import os
import sys
import json
import asyncio
import re
import datetime
import time
import sqlite3
from typing import Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

from openai import OpenAI

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
from common.guardrails import (
    input_guard, tool_call_guard, output_guard,
    input_semantic_guard, output_semantic_guard,
)
from common.model_router import call_with_fallback
from common.pending_tools import pending, save_pending
from common.auth import get_user_info, ROLE_PERMISSIONS
from common.memory import memory


# ==================== 基础路径与全局变量 ====================
UPLOAD_DIR = os.getenv(
    "UPLOAD_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'uploads')
)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 【拆分保留】原 main 里的 _session_temp_files（写后未读，保留以防遗漏）
_session_temp_files: dict = {}


# ==================== LLM 客户端 ====================
_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)


# ==================== V3 系统提示 ====================
SYSTEM_PROMPT = """
你是企业知识库的忠实代言人。基于工具返回的真实数据回答用户。

【回答边界】
你的回答代表知识库中已收录的内容，不代表个人经验、行业惯例或通用常识。
- 知识库有相关内容 → 忠于原文回答，可整理、可举例、可组织语言
- 知识库未覆盖的部分 → 明确说明"知识库中未收录此内容"，不用通用经验补充
- 工具没数据 → 就说没数据，不编造

回答简洁直接；不编造数据与文档编号。
"""


# ==================== 工具审计日志 ====================
def _is_error_result(result) -> bool:
    """判断工具执行结果是否包含错误标识"""
    return ("错误" in str(result)) or ("失败" in str(result))


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


# ==================== 语义分析器 ====================
async def _analyze_query(query: str) -> dict:
    """语义分析器（US-11）：能力筛选 + 格式判断 + 任务性质。"""
    import json as _json
    fallback = {
        "capabilities": ["rag_search"],
        "specified_format": False,
        "task_nature": "",
    }
    try:
        resp = call_with_fallback(
            messages=[
                {"role": "system", "content": (
                    "分析用户问题，返回一个 JSON 对象，包含三个字段：\n"
                    "1. capabilities：完成任务【可能】需要的工具列表（0 个、1 个或多个）\n"
                    "2. specified_format：用户是否明确指定了输出格式或顺序（true / false）\n"
                    "3. task_nature：一句话描述这个任务的本质（字符串）\n\n"
                    "关于 specified_format 的判断：\n"
                    "- true：用户明确说了报告/回答的结构、顺序、章节列表\n"
                    "  例：'格式：1.饼图 2.数据 3.分析'、'先给结论再解释'、'按 A/B/C 三部分输出'\n"
                    "- false：用户只描述了任务内容，没有提到输出的结构或顺序\n\n"
                    "关于 task_nature：\n"
                    "- 用一句话说清：用户想得到什么？这是什么性质的任务？\n"
                    "- 不要规定具体的章节或顺序——那是生成阶段的 LLM 自己该决定的。\n"
                    "- 你只需描述任务的性质，让下游知道该用什么方式对待。\n\n"
                    "只返回 JSON 对象，不要任何解释。\n\n"
                    "可用工具：\n"
                    "- rag_search：查企业知识库（故障排查、IT 支持、流程规定、FAQ）\n"
                    "- query_database：查内置 SQLite 库（员工、薪资、日程）\n"
                    "- file_analysis：分析上传的 CSV/Excel 数据文件\n"
                    "- web_search：实时互联网信息（新闻、天气、股价）\n"
                    "- schedule：日程增删查\n"
                    "- send_email：发送邮件\n\n"
                    "示例：\n"
                    "Q: '查询员工工资' → "
                    '{"capabilities": ["query_database"], "specified_format": false, '
                    '"task_nature": "简单的数据查询"}\n'
                    "Q: '各月咖啡销售趋势并画出饼图' → "
                    '{"capabilities": ["file_analysis"], "specified_format": false, '
                    '"task_nature": "一份多维度的数据分析报告，含可视化"}\n'
                    "Q: '各月咖啡销售趋势并画出饼图，格式：1.饼图 2.数据 3.分析' → "
                    '{"capabilities": ["file_analysis"], "specified_format": true, '
                    '"task_nature": "用户已指定三段式结构的数据分析报告"}\n'
                    "Q: '查工资最高的人，然后搜他职位新闻，发邮件给 x@y.com' → "
                    '{"capabilities": ["query_database", "web_search", "send_email"], '
                    '"specified_format": false, "task_nature": "跨工具的多步骤任务"}\n'
                    "Q: '你好' → "
                    '{"capabilities": [], "specified_format": false, '
                    '"task_nature": "闲聊，无需工具"}'
                )},
                {"role": "user", "content": query}
            ],
            task_type="analysis",
            complexity="normal",
            max_tokens=180,
            temperature=0
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        data = _json.loads(raw)
        if not isinstance(data, dict):
            print("###语义分析### 返回非 dict，降级 fallback")
            return fallback
        caps = data.get("capabilities", [])
        if not isinstance(caps, list):
            caps = []
        caps = [c for c in caps if isinstance(c, str)]
        specified = bool(data.get("specified_format", False))
        nature = data.get("task_nature", "")
        if not isinstance(nature, str):
            nature = ""
        nature = nature.strip()[:200]
        print(f"###语义分析### '{query[:30]}...' -> caps={caps}, "
              f"specified_format={specified}, task_nature='{nature}'")
        return {
            "capabilities": caps,
            "specified_format": specified,
            "task_nature": nature,
        }
    except Exception as e:
        print(f"###语义分析### 失败: {e}，降级 fallback")
        return fallback


# ==================== 数据源发现 ====================
def _discover_data_sources() -> str:
    """动态发现所有可用数据源，返回结构化目录文本（AI Native 2.0）。"""
    lines = []
    lines.append("【可用数据源目录】")

    lines.append("\n▶ 内置 SQLite 数据库：")
    db_names = ["sample.db", "calendar.db", "users.db", "health.db", "feedback.db"]
    found_db = False
    for db_name in db_names:
        db_path = os.path.join(UPLOAD_DIR, db_name)
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                cur.execute(f"PRAGMA table_info({t})")
                cols = cur.fetchall()
                col_desc = ", ".join(f"{c[1]}({c[2]})" for c in cols)
                lines.append(f"  - {db_name} → 表 `{t}`：{col_desc}")
                found_db = True
            conn.close()
        except Exception as e:
            lines.append(f"  - {db_name}（读取失败：{e}）")
    if not found_db:
        lines.append("  （无）")

    rag_file = os.path.join(UPLOAD_DIR, "rag_data.json")
    if os.path.exists(rag_file):
        try:
            with open(rag_file, "r", encoding="utf-8") as f:
                store = json.load(f)
            file_lines = []
            for item in store.get("files", []):
                schema = item.get("schema")
                if not schema:
                    continue
                fname = item.get("file_name", "")
                tags = item.get("tags", "")
                cols = [c.get("name", "") for c in schema.get("columns", [])]
                file_lines.append(f"  - {fname}（标签：{tags}，列：{', '.join(cols)}）")
            if file_lines:
                lines.append("\n▶ 已上传的数据文件：")
                lines.extend(file_lines)
        except Exception as e:
            lines.append(f"\n▶ 已上传的数据文件（读取失败：{e}）")

    lines.append(
        "\n【选择提示】根据用户问题，从上表中选择最相关的数据源。"
        "例如：问员工工资 → sample.db / employees 表；"
        "问咖啡销售 → coffee_sales.csv。"
        "若用户问题与任何数据源无关，请不要调用 query_database 或 file_analysis。"
    )
    return "\n".join(lines)


# ==================== 物理层背景检索 ====================
def _retrieve_background(query: str) -> dict:
    """【检索基础设施化】物理层无条件执行企业知识库检索。"""
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


# ==================== 上下文防爆 ====================
def _shrink_messages(messages, max_chars=1500000):
    total_chars = 0
    kept = []
    for m in reversed(messages):
        content = str(m.get("content", ""))
        if total_chars + len(content) > max_chars and m.get("role") != "system":
            print(f"###上下文裁剪### 触发裁剪，丢弃旧消息，当前总字符数: {total_chars}")
            break
        kept.append(m)
        total_chars += len(content)
    return list(reversed(kept))


# ==================== 主流程编排 ====================
async def run_orchestration(session_id: str, query: str, user_text: str = None,
    query_worker=None, command_worker=None, TOOL_ROUTER=None,
    image_base64: str = None, temp_file_path: str = None):
    """
    V3 架构（流式版）：schema 事实注入 + LLM 自主决策 + 确定性执行
    （US-13 拆分前为 main.py::chat_core_stream，逐字搬运）
    """
    # ① 前置管道：用户状态校验与安全过滤
    real_username = session_id.split('_')[0] if '_' in session_id else session_id
    user_info = get_user_info(real_username)

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

    # 第一层：字符串过滤
    is_safe, err_msg = input_guard(query)
    if not is_safe:
        yield json.dumps({"type": "status", "content": err_msg}, ensure_ascii=False)
        return

    # 第二层：语义审核（US-14 方案 A：串行）
    yield json.dumps({"type": "status", "content": "正在审核输入..."}, ensure_ascii=False)
    is_safe_sem, err_msg_sem = await input_semantic_guard(query)
    if not is_safe_sem:
        simple_log_tool(session_id, query, "input_guard", {}, "【语义拦截】")
        yield json.dumps({"type": "answer", "content": err_msg_sem, "contexts": []}, ensure_ascii=False)
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

        _ext = os.path.splitext(temp_file_path)[1].lower()
        if _ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
            try:
                _ocr = await asyncio.to_thread(ocr_image, temp_file_path)
                print(f"###文件预处理### 图片 OCR 完成，{len(str(_ocr))} 字符")
                query = f"{query}\n\n【上传图片的 OCR 识别结果】\n{_ocr}"
            except Exception as e:
                print(f"###文件预处理### OCR 失败: {e}")
        elif _ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
            try:
                _asr = await asyncio.to_thread(speech_to_text, temp_file_path)
                print(f"###文件预处理### 音频转写完成，{len(str(_asr))} 字符")
                query = f"{query}\n\n【上传音频的转写结果】\n{_asr}"
            except Exception as e:
                print(f"###文件预处理### ASR 失败: {e}")
        elif _ext in ('.csv', '.xlsx', '.xls'):
            try:
                _csv = await asyncio.to_thread(analyze_file, temp_file_path)
                print(f"###文件预处理### 表格分析完成，{len(str(_csv))} 字符")
                query = f"{query}\n\n【上传表格的分析结果】\n{_csv}"
            except Exception as e:
                print(f"###文件预处理### 表格分析失败: {e}")
    else:
        _session_temp_files.pop(session_id, None)
        current_temp_file = None

    if any(kw in query for kw in ["现在几点", "现在时间", "几点了", "什么时间", "当前时间"]):
        time_result = get_current_time()
        answer = f"现在是 {time_result}（北京时间）。"
        simple_log_tool(session_id, original_query, "get_current_time", {}, time_result)
        memory.append(session_id, history_text, answer)
        yield json.dumps({"type": "answer", "content": output_guard(answer), "contexts": []}, ensure_ascii=False)
        return

    history = memory.get(session_id)[-20:]

    yield json.dumps({"type": "status", "content": "正在分析任务..."}, ensure_ascii=False)
    analysis = await _analyze_query(query)
    capabilities = analysis["capabilities"]
    specified_format = analysis["specified_format"]
    task_nature = analysis.get("task_nature", "")
    system_content = SYSTEM_PROMPT

    if task_nature:
        system_content += f"\n\n【任务性质】{task_nature}"
        print(f"###任务性质### 注入 '{task_nature}'")

    need_data_sources = any(
        c in capabilities for c in ["query_database", "file_analysis"]
    )
    if need_data_sources:
        data_sources = _discover_data_sources()
        system_content += "\n\n" + data_sources
        print(f"###数据源目录### 注入 {len(data_sources)} 字符")
    else:
        print(f"###数据源目录### 跳过（能力={capabilities}）")

    if current_temp_file:
        raw_name = os.path.basename(current_temp_file)
        if raw_name.startswith(session_id + "_"):
            raw_name = raw_name[len(session_id) + 1:]
        if len(raw_name) > 9 and raw_name[8] == '_':
            raw_name = raw_name[9:]
        source_prefix = f"根据上传文件 {raw_name} 和工具返回的真实数据，"
    elif "rag_search" in capabilities:
        source_prefix = "根据企业知识库文档和工具返回的真实数据，"
    elif "query_database" in capabilities or "file_analysis" in capabilities:
        source_prefix = "根据数据查询结果，"
    elif "web_search" in capabilities:
        source_prefix = "根据实时信息，"

    bg = {"text": "", "ids": []}
    if "rag_search" in capabilities:
        yield json.dumps({"type": "status", "content": "正在检索企业知识库..."}, ensure_ascii=False)
        bg = _retrieve_background(query)
        if bg["text"]:
            system_content += f"\n\n【企业知识库资料】\n{bg['text']}"
            print(f"###背景检索### 命中 {len(bg['ids'])} 个 ID: {bg['ids']}")
        else:
            system_content += (
                "\n\n【企业知识库资料】\n"
                "（系统已执行检索，未命中任何相关文档。）"
            )
            print(f"###背景检索### 无相关命中")

    system_content += "\n\n【回答要求】请不要在回答开头写任何关于数据来源的说明。直接以'以下是...'开头。系统会自动为你添加前缀。"

    current_date_str = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y年%m月%d日 %H:%M:%S")
    system_content += f"\n\n【系统时间信息】当前系统时间是 {current_date_str}。请以此时间为准，不要依赖你的内部知识库或历史记忆。"

    role = user_info.get("role", "viewer") if user_info else "viewer"
    if role not in ROLE_PERMISSIONS:
        role = "manager"

    allowed_tools = TOOLS_METADATA

    messages = [{"role": "system", "content": system_content}]
    if "web_search" not in capabilities:
        messages.extend(history)
    messages.append({"role": "user", "content": query})
    messages = _shrink_messages(messages)

    image_outputs = []
    collected_sources = list(bg["ids"])

    MAX_ITERATIONS = 8
    MAX_RETRIES = 2
    MAX_SEARCH_CALLS = 5          # US-16：单次对话检索上限
    search_call_count = 0

    for iteration in range(MAX_ITERATIONS):
        t_llm = time.time()
        response = None
        last_error = None

        for llm_retry in range(1):
            try:
                response = call_with_fallback(
                    messages=messages,
                    task_type="tool_use",
                    complexity="normal",
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

        t_llm_cost = round(time.time() - t_llm, 3)
        tool_trace.append({
            "iteration": iteration, "stage": "llm",
            "cost_seconds": t_llm_cost,
            "has_tool_calls": bool(msg.tool_calls),
        })

        if not msg.tool_calls:
            answer = msg.content
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
                    "content": "你刚才的回答没有调用任何工具。请重新审视用户的问题，判断是否需要调用工具。可用工具及其适用场景见工具描述，请根据用户意图自主选择最合适的工具。"
                })
                continue

            yield json.dumps({"type": "status", "content": "正在生成最终报告..."}, ensure_ascii=False)
            break

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

            # US-16：检索次数上限保护
            if func_name == "search_knowledge":
                search_call_count += 1
                if search_call_count > MAX_SEARCH_CALLS:
                    result = (
                        f"【检索上限】单次对话最多检索 {MAX_SEARCH_CALLS} 次，"
                        f"请基于已有信息回答，或告知用户当前资料不足。"
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
                    simple_log_tool(session_id, original_query, func_name, arguments, "【检索上限】")
                    continue

            if func_name in ["add_event", "delete_event", "list_events"]:
                arguments["_tenant"] = memory.get_tenant(session_id)

            user_allowed_tools = ROLE_PERMISSIONS.get(role, [])
            has_permission = ("*" in user_allowed_tools) or (func_name in user_allowed_tools)
            if role == "viewer" and func_name in ["web_search", "fetch_webpage"]:
                has_permission = False

            if not has_permission:
                result = f"【物理层安全拦截】当前账号（{role}）没有调用 {func_name} 的权限。请直接告知用户无权使用该功能，不要解释原因。"
                messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result})
                simple_log_tool(session_id, original_query, func_name, arguments, "【权限拦截】")
                continue

            if func_name in ["aggregate", "generate_chart"] and current_temp_file and not arguments.get("file_name"):
                arguments["file_name"] = os.path.basename(current_temp_file)

            if func_name == "generate_chart":
                arguments["_user_query"] = original_query

            yield json.dumps({"type": "tool", "content": f"正在调用工具: {func_name}，请稍候..."}, ensure_ascii=False)
            await asyncio.sleep(0.1)

            result = None
            t_tool = time.time()
            for retry in range(MAX_RETRIES + 1):
                try:
                    if func_name in AVAILABLE_TOOLS:
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
                            result = await asyncio.to_thread(AVAILABLE_TOOLS[func_name], **arguments)
                    else:
                        result = f"未找到工具 {func_name}"
                    break
                except Exception as e:
                    if retry == MAX_RETRIES:
                        result = f"工具执行错误（已重试 {MAX_RETRIES} 次）: {e}"
                    else:
                        await asyncio.sleep(0.5)

            tool_trace.append({
                "iteration": iteration, "stage": "tool",
                "name": func_name, "cost_seconds": round(time.time() - t_tool, 3),
                "result_len": len(str(result)),
            })

            if func_name in ["generate_chart", "generate_image"] and result:
                for m in re.finditer(
                    r'(/charts/|/images/)[a-f0-9]+\.png',
                    str(result)
                ):
                    url = m.group(0)
                    if url not in image_outputs:
                        image_outputs.append(url)
                if image_outputs:
                    print(f"###图片记录### 已记录 {len(image_outputs)} 张图片作为兜底素材")

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

    answer = output_guard(answer)

    # 语义审核（US-14 方案 A：串行）
    yield json.dumps({"type": "status", "content": "正在审核输出..."}, ensure_ascii=False)
    is_safe_out, err_msg_out = await output_semantic_guard(answer, original_query)
    if not is_safe_out:
        simple_log_tool(session_id, original_query, "output_guard", {}, "【语义拦截】")
        answer = err_msg_out

    answer = re.sub(r'!\[[^\]]*\]\((?!\/charts\/|\/images\/)[^)]*\)', '', answer)

    answer = re.sub(r'^(根据|基于).*?数据，|根据数据文件[^\n]*?，', '', answer).strip()

    if source_prefix and answer.startswith(source_prefix):
        answer = answer[len(source_prefix):].strip()
    if source_prefix and answer.startswith("#"):
        answer = source_prefix + "\n\n" + answer
    else:
        answer = source_prefix + answer

    if image_outputs:
        real_img_pattern = r'!\[[^\]]*\]\(\.?(/charts/|/images/)[a-fA-F0-9]+\.png\)'
        kept_urls = re.findall(real_img_pattern, answer)
        if kept_urls:
            print(f"###图片处理### LLM 引用 {len(kept_urls)} 张图片，位置由 LLM 决定")
        else:
            img_md = "\n\n".join(f"![图表]({url})" for url in image_outputs)
            answer = img_md + "\n\n" + answer.lstrip()
            print(f"###图片处理### LLM 未引用任何图片，兜底插入 {len(image_outputs)} 张到顶部")

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

    chunk_size = 2
    for i in range(0, len(answer), chunk_size):
        chunk = answer[i:i+chunk_size]
        context_data = collected_sources if i == 0 else []
        yield json.dumps({"type": "answer", "content": chunk, "contexts": context_data}, ensure_ascii=False)
        await asyncio.sleep(0.01)