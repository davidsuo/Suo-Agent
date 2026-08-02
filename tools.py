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
import replicate

# 最近上传的文件路径（用于 analyze_file 自动使用）
last_uploaded_file = None

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

# ---------- 网页搜索 ----------
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

# ---------- Python 安全执行器 ----------
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

def speech_to_text(audio_file_path: str) -> str:
    token = get_baidu_access_token()
    if not token:
        return "语音识别未配置或凭证无效"

    try:
        import soundfile as sf
        from scipy import signal
        import tempfile
        data, original_rate = sf.read(audio_file_path)
        if len(data.shape) > 1:
            data = data[:, 0]
        if original_rate != 16000:
            num_samples = int(len(data) * 16000 / original_rate)
            data = signal.resample(data, num_samples)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, data, 16000, subtype='PCM_16')
        tmp.close()
        processed_path = tmp.name
    except Exception as e:
        return f"音频预处理失败: {e}"

    MAX_SIZE_BYTES = 1_900_000
    try:
        file_size = os.path.getsize(processed_path)
        if file_size > MAX_SIZE_BYTES:
            os.remove(processed_path)
            return "语音识别失败: 音频文件过大，请录制不超过 60 秒的短语音。"
    except Exception as e:
        return f"文件大小检查失败: {e}"

    try:
        with open(processed_path, "rb") as f:
            audio_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"音频文件读取失败: {e}"
    finally:
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
        "len": file_size,
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

# ---------- 文件分析（自动使用最近文件） ----------
def analyze_file(file_path: str = None) -> str:
    global last_uploaded_file
    if not file_path:
        if last_uploaded_file:
            file_path = last_uploaded_file
        else:
            return "错误：没有已上传的文件，请先上传文件。"

    try:
        import pandas as pd
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file_path)
        else:
            return "不支持的文件格式"

        rows = len(df)
        info = f"文件分析结果：\n- 行数: {rows}\n- 列数: {len(df.columns)}\n"
        info += f"- 列名: {', '.join(df.columns.tolist())}\n"

        # 大文件（>500行）仅展示前3行和关键统计
        if rows > 500:
            info += "\n⚠️ 文件较大，仅展示前3行和关键信息。\n"
            info += f"数据类型:\n{df.dtypes.to_string()}\n\n"
            info += "前3行数据:\n"
            info += df.head(3).to_string(index=False)
            # 如果存在 price 列，快速求出最高价及对应品种
            if 'price' in df.columns:
                max_price = df['price'].max()
                max_row = df[df['price'] == max_price]
                if 'coffee_name' in df.columns:
                    top_names = max_row['coffee_name'].unique()
                    info += f"\n\n🏆 最贵咖啡价格: {max_price}，品种: {', '.join(top_names)}"
                else:
                    info += f"\n\n🏆 最高价格: {max_price}"
            return info

        # 小文件完整分析
        info += f"数据类型:\n{df.dtypes.to_string()}\n\n"
        info += "前5行数据:\n"
        info += df.head(5).to_string(index=False)
        num_cols = df.select_dtypes(include='number')
        if not num_cols.empty:
            info += "\n\n数值列统计:\n"
            info += num_cols.describe().to_string()
            # 如果存在 price 和 coffee_name，直接给出最贵品种
            if 'price' in df.columns and 'coffee_name' in df.columns:
                max_price = df['price'].max()
                top_coffee = df[df['price'] == max_price]['coffee_name'].unique()
                info += f"\n\n🏆 最贵咖啡: {', '.join(top_coffee)}，价格: {max_price}"
        return info
    except Exception as e:
        return f"文件分析失败: {e}"
        
        
# ---------- Replicate生成图像 ----------
def generate_image(prompt: str, negative_prompt: str = "") -> str:
    """使用 Stable Diffusion 生成图片，返回图片 URL"""
    api_token = os.getenv("REPLICATE_API_TOKEN")
    if not api_token:
        return "图像生成未配置（缺少 REPLICATE_API_TOKEN）"
    try:
        output = replicate.run(
            "stability-ai/stable-diffusion:27b93a2413e7f36cd83da926f3656280b2931564ff050bf9575f08f04623e0fe",
            input={
                "prompt": prompt,
                "negative_prompt": negative_prompt or "",
                "num_outputs": 1,
                "width": 512,
                "height": 512,
            }
        )
        # output 是列表，第一个元素是图片 URL
        if output and len(output) > 0:
            return f"图片已生成：{output[0]}"
        else:
            return "图像生成失败：未返回图片"
    except Exception as e:
        return f"图像生成错误: {e}"


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
        }
    },
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
    },
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
    },
    {
        "type": "function",
        "function": {
            "name": "speech_to_text",
            "description": "将用户上传的音频文件转写为文本，支持中文普通话",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_file_path": {"type": "string", "description": "音频文件的本地路径"}
                },
                "required": ["audio_file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_file",
            "description": "分析用户上传的 CSV 或 Excel 文件，返回摘要信息。若未提供文件路径，则自动分析最近上传的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "上传文件的本地路径（可选，不填则使用最近上传的文件）"
                    }
                }
                # 注意：不再要求 file_path 必填
            }
        }
    }
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "使用 Stable Diffusion 根据文字描述生成一张图片，返回图片的 URL。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "英文或中文的图片描述，例如 'a cat sitting on a cloud'"
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "可选的负面提示，描述不希望出现在图片中的内容"
                    }
                },
                "required": ["prompt"]
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
    "execute_python": execute_python,
    "speech_to_text": speech_to_text,
    "analyze_file": analyze_file,
    "generate_image": generate_image
}