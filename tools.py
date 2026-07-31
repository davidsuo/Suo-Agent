import datetime
import sqlite3
import smtplib
import os
import sys
from io import StringIO
import traceback
import requests
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from ddgs import DDGS


# ---------- 基础工具 ----------
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

# ---------- DDGS 网页搜索 ----------
def web_search(query: str, max_results: int = 5):
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "未找到相关搜索结果。"
            formatted = []
            for r in results:
                title = r.get("title", "")
                href = r.get("href", "")
                body = r.get("body", "")
                formatted.append(f"标题: {title}\n链接: {href}\n摘要: {body}\n")
            return "\n".join(formatted)
    except Exception as e:
        return f"搜索失败: {e}"

# ---------- 安全 Python 执行器 ----------
def execute_python(code: str):
    safe_builtins = {
        "print": print, "range": range, "len": len, "int": int, "float": float,
        "str": str, "list": list, "dict": dict, "abs": abs, "min": min,
        "max": max, "sum": sum, "round": round, "sorted": sorted,
        "enumerate": enumerate, "zip": zip, "type": type, "isinstance": isinstance,
    }
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        exec(code, {"__builtins__": safe_builtins}, {})
        output = captured.getvalue()
        if not output.strip():
            output = "代码执行完毕，无输出。"
        return output
    except Exception as e:
        return f"代码执行错误: {traceback.format_exc()}"
    finally:
        sys.stdout = old_stdout

# ---------- 百度语音转写 ----------
def get_baidu_access_token() -> str:
    api_key = os.getenv("BAIDU_ASR_API_KEY")
    secret_key = os.getenv("BAIDU_ASR_SECRET_KEY")
    if not api_key or not secret_key:
        return ""
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        return data.get("access_token", "")
    except Exception:
        return ""

from pydub import AudioSegment

def speech_to_text(audio_file_path: str) -> str:
    token = get_baidu_access_token()
    if not token:
        return "语音识别未配置或凭证无效"

    # 使用 pydub 转换为 16kHz, 单声道, 16bit WAV
    try:
        audio = AudioSegment.from_file(audio_file_path)
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        # 保存到临时文件
        converted_path = audio_file_path + "_conv.wav"
        audio.export(converted_path, format="wav")
        processed_path = converted_path
    except Exception as e:
        return f"音频预处理失败: {e}"

    # 检查文件大小（百度要求单次识别不超过 60 秒，约 1.9MB）
    MAX_SIZE_BYTES = 1_900_000
    try:
        file_size = os.path.getsize(processed_path)
        if file_size > MAX_SIZE_BYTES:
            os.remove(processed_path) if os.path.exists(processed_path) else None
            return "语音识别失败: 音频文件过大，请录制不超过 60 秒的短语音。"
    except Exception as e:
        return f"音频文件大小检查失败: {e}"

    try:
        with open(processed_path, "rb") as f:
            audio_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"音频文件读取失败: {e}"
    finally:
        # 清理临时文件
        if os.path.exists(processed_path):
            os.remove(processed_path)

    url = "https://vop.baidu.com/server_api"
    payload = {
        "format": "wav",
        "rate": 16000,
        "channel": 1,
        "cuid": "ai-agent",
        "token": token,
        "speech": audio_base64,
        "len": file_size,      # 原始字节数
        "lan": "zh"
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()
        if data.get("err_no") == 0:
            return "".join(data.get("result", []))
        else:
            return f"语音识别失败: {data.get('err_msg', '未知错误')}"
    except Exception as e:
        return f"语音识别请求错误: {e}"
        
# ---------- 文件分析工具 ----------
def analyze_file(file_path: str) -> str:
    """
    分析 CSV 或 Excel 文件，返回基本信息和前5行数据。
    """
    try:
        import pandas as pd
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path)
        else:
            return "不支持的文件格式，请上传 CSV 或 Excel 文件。"

        # 获取基本统计信息
        info = f"文件分析结果：\n"
        info += f"- 行数: {len(df)}\n"
        info += f"- 列数: {len(df.columns)}\n"
        info += f"- 列名: {', '.join(df.columns.tolist())}\n"
        info += f"- 数据类型:\n{df.dtypes.to_string()}\n\n"
        info += "前5行数据:\n"
        info += df.head(5).to_string(index=False)
        # 可选：描述性统计
        if any(df.select_dtypes(include='number').columns):
            info += "\n\n数值列统计:\n"
            info += df.describe().to_string()
        return info
    except Exception as e:
        return f"文件分析失败: {e}"

# ---------- 工具元数据 ----------
TOOLS_METADATA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "获取当前的日期和时间",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    }
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
    }
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
    }
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
        }
    }
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取实时信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "max_results": {"type": "integer", "description": "返回结果数量，默认5"}
                },
                "required": ["query"]
            }
        }
    }
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": "安全执行 Python 代码并返回输出",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "要执行的 Python 代码"}
                },
                "required": ["code"]
            }
        }
    }
    {
        "type": "function",
        "function": {
            "name": "speech_to_text",
            "description": "将用户上传的音频文件转写为文本，支持中文普通话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_file_path": {
                        "type": "string",
                        "description": "音频文件的本地路径"
                    }
                },
                "required": ["audio_file_path"]
            }
        }
    }
    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": "分析用户上传的 CSV 或 Excel 文件，返回行数、列名、前几行数据和统计信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "上传文件的本地路径"
                    }
                },
                "required": ["file_path"]
            }
        }
    }
]

AVAILABLE_TOOLS = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "send_email": send_email,
    "web_search": web_search,
    "execute_python": execute_python,
    "speech_to_text": speech_to_text，
    "analyze_file": analyze_file,
}