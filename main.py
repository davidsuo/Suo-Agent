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
)

from guardrails import input_guard, tool_call_guard, output_guard
from pending_tools import pending
import asyncio
from agents import SearchWorker, CodeWorker, DataWorker

# 定义各 Worker 的工具字典
image_worker_tools = {
    "generate_image": generate_image,
}

web_scraper_tools = {
    "fetch_webpage": fetch_webpage
}

search_worker_tools = {
    "web_search": web_search,
    "speech_to_text": speech_to_text,
    "get_current_time": get_current_time,
    "calculator": calculator,
}
code_worker_tools = {
    "execute_python": execute_python,
}
data_worker_tools = {
    "query_database": query_database,
    "analyze_file": analyze_file,
}

# 读取 Worker 环境变量
search_worker = SearchWorker("SearchWorker", search_worker_tools)
code_worker = CodeWorker("CodeWorker", code_worker_tools)
data_worker = DataWorker("DataWorker", data_worker_tools)
web_scraper_worker = SearchWorker("WebScraperWorker", web_scraper_tools)   # 复用 SearchWorker 类
image_worker = SearchWorker("ImageWorker", image_worker_tools)  # 复用 SearchWorker 类

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
- generate_image: 根据文字描述生成图片（建议使用英文提示词）

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

# ========== 专业 Worker 初始化 ==========
search_worker_tools = {
    "web_search": web_search,
    "speech_to_text": speech_to_text,
    "get_current_time": get_current_time,
    "calculator": calculator,
}
code_worker_tools = {
    "execute_python": execute_python,
}
data_worker_tools = {
    "query_database": query_database,
    "analyze_file": analyze_file,
}


# 工具名 → Worker 映射（send_email 不在此列，由 Conductor 直接执行）
TOOL_ROUTER = {}
# 更新 TOOL_ROUTER
for name in image_worker.tools:
    TOOL_ROUTER[name] = image_worker
for name in web_scraper_worker.tools:
    TOOL_ROUTER[name] = web_scraper_worker
for name in search_worker.tools:
    TOOL_ROUTER[name] = search_worker
for name in code_worker.tools:
    TOOL_ROUTER[name] = code_worker
for name in data_worker.tools:
    TOOL_ROUTER[name] = data_worker


    
async def generate_plan(user_query, history, client):
    try:
        prompt = f"""
你是一个任务规划器。根据用户的需求，生成一个 JSON 格式的执行计划。
当前可用的工具及**必须遵守的参数**：
- query_database: 查询员工数据库（参数必须为 "sql"，例如 {{"sql": "SELECT * FROM employees"}}）
- web_search: 搜索互联网（参数必须为 "query" 和可选的 "max_results"）
- fetch_webpage: 抓取指定 URL 的网页全文（参数必须为 "url"）
- execute_python: 安全执行 Python 代码（参数必须为 "code"，**仅用于独立计算，不得使用外部数据**）
- get_current_time: 获取当前时间（无参数）
- calculator: 数学计算（参数必须为 "expression"，例如 {{"expression": "(3+5)*2"}}）
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
1. 任何数据统计、计算工资总和等**数学运算，必须使用 query_database 的 SQL 聚合函数（如 SUM、AVG）或 calculator 工具**，严禁使用 execute_python 处理来自数据库的数据。
2. 如果步骤需要用到前一步的结果，请在 arguments 中使用占位符 {{step_X_result}}（X 是步骤id），**严禁在 execute_python 的 code 参数中假设变量已存在**。
3. send_email 必须放在最后一个步骤，且需要用户确认。
4. 只返回 JSON 数组，不要有任何额外文字。

用户需求：{user_query}

正确示例（查询所有工资并计算总和）：
[
  {{{{ "id": 1, "tool": "query_database", "arguments": {{{{ "sql": "SELECT SUM(salary) FROM employees" }}}}, "depends_on": [], "description": "计算工资总和" }}}}
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

    # 惰性启动所有专业 Worker（仅首次调用时执行）
    if not search_worker.is_running:
        asyncio.create_task(search_worker.run_loop())
        search_worker.is_running = True
    if not code_worker.is_running:
        asyncio.create_task(code_worker.run_loop())
        code_worker.is_running = True
    if not data_worker.is_running:
        asyncio.create_task(data_worker.run_loop())
        data_worker.is_running = True
    if not image_worker.is_running:
        asyncio.create_task(image_worker.run_loop())
        image_worker.is_running = True
    if not web_scraper_worker.is_running:
        asyncio.create_task(web_scraper_worker.run_loop())
        web_scraper_worker.is_running = True
    print("Workers ready: Search=True, Code=True, Data=True, Image=True, WebScraper=True")

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
        # ========== 规划引擎执行模式 ==========
        results = {}
        email_args = None
        for step in plan:
            step_id = step["id"]
            tool_name = step["tool"]
            arguments = step["arguments"]

            # 处理参数中的占位符替换
            for dep_id in step.get("depends_on", []):
                if dep_id in results:
                    replacement = str(results[dep_id])
                    for key, val in arguments.items():
                        if isinstance(val, str):
                            arguments[key] = val.replace(f"{{step_{dep_id}_result}}", replacement)

            if tool_name == "send_email":
                email_args = arguments
                continue
            if tool_name == "query_database" and "sql" not in arguments:
                results[step_id] = "错误：query_database 必须提供 'sql' 参数"
                continue
            elif tool_name in TOOL_ROUTER:
                task = {"tool": tool_name, "arguments": arguments}
                max_attempts = 2 if tool_name in ("web_search", "fetch_webpage", "generate_image") else 1
                raw_result = None
                for attempt in range(max_attempts):
                    try:
                        res = await TOOL_ROUTER[tool_name].send_task(task)
                        raw_result = res.get("result", res.get("error")) if res else "未知错误"
                        # 如果结果包含失败关键词，且还有重试机会，则重试
                        if "失败" in str(raw_result) or "错误" in str(raw_result):
                            raise ValueError(f"工具返回失败: {raw_result}")
                        break  # 成功则不重试
                    except Exception as e:
                        if attempt == max_attempts - 1:
                            raw_result = f"任务执行失败（重试{max_attempts}次）: {e}"
                        else:
                            print(f"[规划引擎] 步骤{step_id}失败，重试... ({attempt+1}/{max_attempts})")
                            await asyncio.sleep(1)
                if raw_result is None:
                    raw_result = "未知错误"
                if tool_name == "generate_image" and not str(raw_result).startswith("图像生成"):
                    image_output = raw_result
                    results[step_id] = "图片已生成，将在最终回答中展示。"
                else:
                    results[step_id] = raw_result
                print(f"[规划引擎] 步骤{step_id}完成: {str(results[step_id])[:80]}")

        # 如果有邮件步骤，生成确认提示并持久化
        if email_args:
            body = email_args.get("body", "")
            for step_id, result_text in results.items():
                body = body.replace(f"{{step_{step_id}_result}}", str(result_text))
            email_args["body"] = body

            pending[session_id] = {
                "tool_name": "send_email",
                "arguments": email_args
            }
            # 保存到文件（如果需要持久化）
            # save_pending(pending)

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
            # 记录规划日志
            log_entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "session_id": session_id,
                "query": query,
                "plan": plan,
                "results": {str(k): str(v)[:200] for k, v in results.items()},  # 截断长结果
                "status": "completed"
            }
            try:
                with open("plan_log.json", "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"规划日志写入失败: {e}")   
                
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
                        max_attempts = 2 if func_name in ("web_search", "fetch_webpage", "generate_image") else 1
                        raw_result = None
                        for attempt in range(max_attempts):
                            try:
                                res = await target_worker.send_task(task)
                                raw_result = res.get("result", res.get("error")) if res else "未知错误"
                                if "失败" in str(raw_result) or "错误" in str(raw_result):
                                    raise ValueError(f"工具返回失败: {raw_result}")
                                break
                            except Exception as e:
                                if attempt == max_attempts - 1:
                                    raw_result = f"任务执行失败（重试{max_attempts}次）: {e}"
                                else:
                                    print(f"[常规模式] {func_name}失败，重试... ({attempt+1}/{max_attempts})")
                                    await asyncio.sleep(1)
                        if raw_result is None:
                            raw_result = "未知错误"
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)