# tools.py
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
import replicate
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from ddgs import DDGS

# 最近上传的文件路径（用于 analyze_file 自动使用）
last_uploaded_file = None

# ---------- 基础工具 ----------
def get_current_time():
    """返回当前日期和时间"""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")

def calculator(expression: str):
    """安全计算数学表达式"""
    try:
        allowed_chars = set("0123456789+-*/().% ^")
        if not all(c in allowed_chars for c in expression.replace(" ", "")):
            return "错误：表达式包含不允许的字符"
        result = eval(expression, {"__builtins__": {}})
        return str(result)
    except Exception as e:
        return f"计算出错: {e}"

def query_database(sql: str):
    """查询 SQLite 数据库，仅允许 SELECT"""
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
    api_key = os.getenv("BREVO_API_KEY")
    from_email = os.getenv("EMAIL_FROM")
    if not api_key or not from_email:
        return "错误：邮件服务未配置（Brevo API Key 或发件人邮箱缺失）。"

    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": api_key,
        "content-type": "application/json"
    }
    payload = {
        "sender": {"email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        if resp.status_code == 201:
            return f"邮件已成功发送给 {to_email}"
        else:
            return f"邮件发送失败: {resp.status_code} {resp.text[:200]}"
    except Exception as e:
        return f"邮件发送错误: {e}"

# ---------- 网页搜索 (DDGS) ----------
def web_search(query: str, max_results: int = 5):
    """使用 DDGS 搜索互联网"""
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
    """在受限沙箱中执行 Python 代码，返回输出"""
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
    """获取百度 access_token"""
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
    """使用百度短语音识别，自动处理音频格式"""
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
            return "不支持的文件格式，请上传 CSV 或 Excel 文件。"

        rows = len(df)
        info = f"文件分析结果：\n- 行数: {rows}\n- 列数: {len(df.columns)}\n"
        info += f"- 列名: {', '.join(df.columns.tolist())}\n"

        # 大文件（>500行）仅展示前3行和关键统计
        if rows > 500:
            info += "\n⚠️ 文件较大，仅展示前3行和关键信息。\n"
            info += f"数据类型:\n{df.dtypes.to_string()}\n\n"
            info += "前3行数据:\n"
            info += df.head(3).to_string(index=False)
            if 'price' in df.columns:
                max_price = df['price'].max()
                max_row = df[df['price'] == max_price]
                if 'coffee_name' in df.columns:
                    top_names = max_row['coffee_name'].unique()
                    info += f"\n\n🏆 最贵咖啡价格: {max_price}，品种: {', '.join(top_names)}"
                else:
                    info += f"\n\n🏆 最高价格: {max_price}"
            return info

        info += f"数据类型:\n{df.dtypes.to_string()}\n\n"
        info += "前5行数据:\n"
        info += df.head(5).to_string(index=False)
        num_cols = df.select_dtypes(include='number')
        if not num_cols.empty:
            info += "\n\n数值列统计:\n"
            info += num_cols.describe().to_string()
            if 'price' in df.columns and 'coffee_name' in df.columns:
                max_price = df['price'].max()
                top_coffee = df[df['price'] == max_price]['coffee_name'].unique()
                info += f"\n\n🏆 最贵咖啡: {', '.join(top_coffee)}，价格: {max_price}"
        return info
    except Exception as e:
        return f"文件分析失败: {e}"

# ---------- 图像生成 (Stable Diffusion via Replicate) ----------
def generate_image(prompt: str, negative_prompt: str = "") -> str:
    # 临时降级，避免超时
    return "图像生成服务暂时不可用，请稍后重试或使用其他平台。"

    API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-2-1"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "negative_prompt": negative_prompt or "",
            "width": 512,
            "height": 512,
            "num_inference_steps": 30,
        }
    }

    try:
        resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
        if resp.status_code == 200:
            # 返回的是图片二进制数据，我们需要上传到临时图片托管或直接返回 base64
            # 简单方案：将图片保存到 Render 的临时文件，返回 URL
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(resp.content)
                temp_path = f.name
            # 由于 Render 无法直接提供临时文件访问，我们改为返回 base64 图像
            import base64
            img_base64 = base64.b64encode(resp.content).decode("utf-8")
            return f"图片已生成（base64）：![生成图片](data:image/png;base64,{img_base64})"
        else:
            return f"图像生成失败: {resp.status_code} {resp.text[:200]}"
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
    },
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