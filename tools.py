# tools.py
import datetime
import sqlite3
import smtplib
import os
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import sys
from io import StringIO
import traceback

# ---------- 原有工具 ----------
def get_current_time():
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")

def calculator(expression: str):
    try:
        allowed_chars = set("0123456789+-*/().% ^")
        if not all(c in allowed_chars for c in expression.replace(" ", "")):
            return "错误：表达式包含不允许的字符"
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"计算出错: {e}"

def query_database(sql: str):
    db_path = "sample.db"
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：仅允许执行 SELECT 查询"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        conn.close()
        if not rows:
            return "查询结果为空"
        result = " | ".join(columns) + "\n"
        result += "\n".join([" | ".join(map(str, row)) for row in rows])
        return result
    except Exception as e:
        return f"数据库查询错误: {e}"

def send_email(to_email: str, subject: str, body: str):
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    if not sender or not password:
        return "错误：邮件服务未配置。"
    try:
        msg = MIMEMultipart()
        msg['From'] = sender
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        server.quit()
        return f"邮件已成功发送给 {to_email}"
    except Exception as e:
        return f"邮件发送失败: {e}"

# ---------- 新增工具：网页搜索 ----------

def web_search(query: str, max_results: int = 5):
    """
    使用 SearXNG 公共实例搜索，失败时回退到模拟结果。
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    searx_instances = [
        "https://searx.be/search",
        "https://search.sapti.me/search",
        "https://searx.tiekoetter.com/search",
        "https://search.bus-hit.me/search"
    ]

    for instance in searx_instances:
        try:
            params = {
                "q": query,
                "format": "json",
                "pageno": 1,
                "language": "zh-CN",
            }
            resp = requests.get(instance, params=params, headers=headers, timeout=10)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/json"):
                data = resp.json()
                results = data.get("results", [])[:max_results]
                if results:
                    formatted = []
                    for r in results:
                        title = r.get("title", "")
                        url = r.get("url", "")
                        snippet = r.get("content") or r.get("snippet") or r.get("description") or ""
                        formatted.append(f"标题: {title}\n链接: {url}\n摘要: {snippet}\n")
                    return "\n".join(formatted)
        except Exception:
            continue

    # 所有实例失败，使用模拟结果
    mock_results = [
        {"title": "OpenAI 发布 GPT-5 预览版", "url": "https://example.com/gpt5", "content": "OpenAI 今日发布 GPT-5 预览版，性能大幅提升。"},
        {"title": "DeepSeek 开源最新多模态模型", "url": "https://example.com/deepseek", "content": "DeepSeek 团队宣布开源新一代多模态模型。"},
        {"title": "苹果发布 M4 芯片 MacBook", "url": "https://example.com/m4", "content": "苹果推出搭载 M4 芯片的 MacBook，续航达20小时。"}
    ]
    formatted = []
    for r in mock_results[:max_results]:
        formatted.append(f"标题: {r['title']}\n链接: {r['url']}\n摘要: {r['content']}\n")
    return "(实时搜索暂时不可用，以下为模拟结果)\n" + "\n".join(formatted)


# ---------- 新增工具：代码解释器（安全沙箱） ----------
def execute_python(code: str):
    """
    安全执行一段 Python 代码，仅允许有限的内建函数。
    返回标准输出内容或错误信息。
    """
    # 只允许使用安全的内建函数，禁止导入、文件操作等
    safe_builtins = {
        "print": print,
        "range": range,
        "len": len,
        "int": int,
        "float": float,
        "str": str,
        "list": list,
        "dict": dict,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "round": round,
        "sorted": sorted,
        "enumerate": enumerate,
        "zip": zip,
        "type": type,
        "isinstance": isinstance,
    }
    # 重定向标准输出以捕获 print 内容
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        # 严格限制执行环境
        exec(code, {"__builtins__": safe_builtins}, {})
        output = captured.getvalue()
        if not output.strip():
            output = "代码执行完毕，无输出。"
        return output
    except Exception as e:
        return f"代码执行错误: {traceback.format_exc()}"
    finally:
        sys.stdout = old_stdout

# ---------- 工具元数据 ----------
TOOLS_METADATA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期和时间",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行数学计算",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "数学表达式，如 '(3+5)*2'"}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "查询本地 SQLite 数据库，表 employees (id, name, position, salary)",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SELECT 查询语句"}
                },
                "required": ["sql"]
            }
        }
    },
    {
                "type": "function",
        "function": {
            "name": "send_email",
            "description": "发送邮件（需要用户确认）",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_email": {"type": "string", "description": "收件人邮箱"},
                    "subject": {"type": "string", "description": "主题"},
                    "body": {"type": "string", "description": "正文"}
                },
                "required": ["to_email", "subject", "body"]
            }
        },
        "dangerous": True   # ← 加在这里，与 function 并列
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取实时信息，如新闻、百科、动态数据等",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "安全执行 Python 代码并返回输出，可用于数据计算、图表生成等",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"}
                },
                "required": ["code"]
            }
        }
    }
]

# 工具名称到函数的映射
AVAILABLE_TOOLS = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "send_email": send_email,
    "web_search": web_search,
    "execute_python": execute_python
}