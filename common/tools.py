# common/tools.py
"""
智能体工具函数库

【V3 架构】
- 后端只提供"原子能力"（如 aggregate 执行确定性聚合）
- 意图理解、参数选择全部由 LLM 通过 function calling 完成
- 不包含任何 "if '月' in query" 这类关键词匹配
"""
import sqlite3
import smtplib
import os
import sys
from io import StringIO
import traceback
import requests
import base64
import json
import re
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from ddgs import DDGS
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Dict, Optional

# ==================== 通用辅助函数 ====================
def _request_with_retry(method: str, url: str, retries: int = 2, **kwargs):
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 15), **kwargs)
            return resp
        except (requests.Timeout, requests.ConnectionError):
            if attempt == retries:
                return None
            time.sleep(1)
    return None

# ==================== 基础工具 ====================
def get_current_time(**kwargs) -> str:
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        now = datetime.utcnow() + timedelta(hours=8)
    return now.strftime("%Y-%m-%d %H:%M:%S")

def calculator(expression: str, **kwargs) -> str:
    try:
        if '\n' in expression:
            lines = expression.strip().split('\n')
            numbers = []
            for line in lines:
                cleaned = ''.join(c for c in line if c.isdigit() or c in '.-')
                if cleaned:
                    try:
                        numbers.append(float(cleaned))
                    except ValueError:
                        continue
            if not numbers:
                return "错误：表达式中未找到有效数字"
            return str(round(sum(numbers), 2))
        else:
            expression = expression.replace(",", "")
            allowed_chars = set("0123456789+-*/().% ^")
            if not all(c in allowed_chars for c in expression.replace(" ", "")):
                return "错误：表达式包含不允许的字符"
            result = eval(expression, {"__builtins__": {}})
            return str(round(float(result), 2))
    except Exception as e:
        return f"计算出错: {e}"

def query_database(sql: str, **kwargs) -> str:
    if not sql.strip().upper().startswith("SELECT"):
        return "错误：仅允许执行 SELECT 查询"
    try:
        with sqlite3.connect("sample.db") as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
        if not rows:
            return "查询结果为空"
        result = " | ".join(columns) + "\n"
        result += "\n".join([" | ".join(map(str, row)) for row in rows])
        return result
    except Exception as e:
        return f"数据库查询错误: {e}"

def send_email(to_email: str, subject: str, body: str, **kwargs) -> str:
    api_key = os.getenv("MAILGUN_API_KEY")
    domain = os.getenv("MAILGUN_DOMAIN")
    from_email = os.getenv("EMAIL_FROM")
    if not api_key or not domain or not from_email:
        return "错误：邮件服务未配置（Mailgun 凭据缺失）"
    url = f"https://api.mailgun.net/v3/{domain}/messages"
    auth = ("api", api_key)
    data = {"from": from_email, "to": [to_email], "subject": subject, "text": body}
    try:
        resp = _request_with_retry("POST", url, retries=2, auth=auth, data=data, timeout=10)
        if resp and resp.status_code == 200:
            return f"邮件已成功发送给 {to_email}"
        return f"邮件发送失败: {resp.status_code if resp else '无响应'}"
    except Exception as e:
        return f"邮件发送错误: {e}"

def web_search(query: str, max_results: int = 5, **kwargs) -> str:
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "未找到相关搜索结果。"
        formatted = []
        for r in results:
            formatted.append(f"标题: {r.get('title', '')}\n链接: {r.get('href', '')}\n摘要: {r.get('body', '')}\n")
        return "\n".join(formatted)
    except Exception as e:
        return f"搜索失败: {e}"

def execute_python(code: str, **kwargs) -> str:
    import matplotlib
    matplotlib.use('Agg')  # 无界面渲染
    safe_builtins = {
        "print": print, "range": range, "len": len, "int": int, "float": float,
        "str": str, "list": list, "dict": dict, "abs": abs, "min": min,
        "max": max, "sum": sum, "round": round, "sorted": sorted,
        "enumerate": enumerate, "zip": zip, "type": type, "isinstance": isinstance,
        "__import__": __import__,  # 允许导入
    }
    # 允许本地导入的模块白名单
    allowed_modules = ["matplotlib", "pandas", "numpy", "json", "base64", "io", "datetime"]
    def safe_import(name, *args, **kwargs):
        if name.split('.')[0] not in allowed_modules:
            raise ImportError(f"不允许导入模块: {name}")
        return __import__(name, *args, **kwargs)
    safe_builtins["__import__"] = safe_import
    
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        # 注入必要的命名空间
        exec(code, {"__builtins__": safe_builtins, "pd": __import__("pandas"), "plt": __import__("matplotlib.pyplot")}, {})
        output = captured.getvalue()
        if not output.strip():
            output = "代码执行完毕，无输出。"
        return output
    except Exception as e:
        return f"代码执行错误: {traceback.format_exc()}"
    finally:
        sys.stdout = old_stdout

# ==================== 百度语音转写 ====================
def get_baidu_access_token() -> str:
    api_key = os.getenv("BAIDU_ASR_API_KEY")
    secret_key = os.getenv("BAIDU_ASR_SECRET_KEY")
    if not api_key or not secret_key:
        return ""
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key}
    try:
        resp = _request_with_retry("GET", url, retries=1, params=params, timeout=10)
        if resp:
            return resp.json().get("access_token", "")
        return ""
    except Exception:
        return ""

def speech_to_text(audio_file_path: str) -> str:
    if audio_file_path.endswith('.webm'):
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(audio_file_path, format="webm")
            wav_path = audio_file_path.replace('.webm', '.wav')
            audio.export(wav_path, format="wav")
            audio_file_path = wav_path
        except Exception as e:
            return f"音频格式转换失败: {e}"
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
    payload = {"format": "wav", "rate": 16000, "channel": 1, "cuid": "ai-agent", "token": token, "speech": audio_base64, "len": file_size, "lan": "zh"}
    try:
        resp = _request_with_retry("POST", url, retries=1, json=payload, timeout=15)
        if resp:
            data = resp.json()
            if data.get("err_no") == 0:
                return "".join(data.get("result", []))
            return f"语音识别失败: {data.get('err_msg', '未知错误')}"
        return "语音识别失败: 网络错误"
    except Exception as e:
        return f"语音识别请求错误: {e}"

# ==================== 文件分析（兼容保留） ====================
def analyze_file(file_path: str, _tenant: str = "default", **kwargs) -> str:
    if not file_path:
        return "错误：请提供文件路径。"
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
        if rows > 500:
            info += "\n⚠️ 文件较大，仅展示前3行。\n"
            info += df.head(3).to_string(index=False)
        else:
            info += "\n前5行数据:\n"
            info += df.head(5).to_string(index=False)
        num_cols = df.select_dtypes(include='number')
        if not num_cols.empty:
            info += "\n\n数值列统计:\n"
            info += num_cols.describe().to_string()
        return info
    except Exception as e:
        return f"文件分析失败: {e}"

# ==================== V3 核心：schema 提取 ====================
def extract_schema(df) -> dict:
    """
    【V3 核心】数据驱动提取 CSV 的 schema，供 LLM 语义理解。
    只做"数据描述"，不做"意图判断"。

    返回：
    {
      "columns": [
        {"name": "date", "type": "date", "samples": ["2024/3/1", "2024/3/2"]},
        {"name": "price", "type": "numeric", "samples": [3.87, 3.38]},
        {"name": "coffee_name", "type": "category", "samples": ["Latte", "Cappuccino"], "cardinality": 12}
      ],
      "row_count": 3636
    }
    """
    import pandas as pd
    columns = []
    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        samples = series.head(3).tolist()
        # 类型判断完全由数据驱动
        col_type = "text"
        if pd.api.types.is_numeric_dtype(series):
            col_type = "numeric"
        else:
            # 尝试日期
            sample = series.head(min(50, len(series)))
            try:
                parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
                valid = parsed.dropna()
                if len(valid) / len(sample) > 0.8 and valid.dt.date.nunique() > 1:
                    col_type = "date"
            except Exception:
                pass
            if col_type == "text":
                nunique = series.nunique()
                if 1 < nunique < 100:
                    col_type = "category"
                else:
                    col_type = "text"
        entry = {"name": str(col), "type": col_type, "samples": samples}
        if col_type == "category":
            entry["cardinality"] = int(series.nunique())
        columns.append(entry)

    return {"columns": columns, "row_count": len(df)}

# ==================== V3 核心：确定性聚合执行器 ====================
def aggregate(
    file_name: str,
    filter_json: str,
    agg_column: str,
    agg_func: str = "sum",
    group_by: str = "",
    **kwargs
) -> str:
    """
    【V3 核心】执行确定性聚合。LLM 通过 function calling 决定：
      - 用哪个文件（file_name）
      - 过滤什么（filter_json）
      - 聚合哪列（agg_column）
      - 怎么聚合（agg_func）
      - 是否分组（group_by）

    filter_json 支持的键（全部可选，LLM 按需填）：
      - year: 2024
      - month: 7
      - quarter: 3
      - start_date: "2024-01-01"
      - end_date: "2024-06-30"
      - <column_name>: <value>  直接按分类列过滤，如 {"coffee_name": "Latte"}

    agg_func 支持：sum / avg / count / max / min
    """
    import pandas as pd
    import numpy as np

    # ---- 1. 定位文件（uploads/temp 或 uploads） ----
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates_dirs = [
        os.path.join(base, "uploads", "temp"),
        os.path.join(base, "uploads"),
    ]
    file_path = None
    for d in candidates_dirs:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if f == file_name or f.endswith("_" + file_name):
                file_path = os.path.join(d, f)
                break
        if file_path:
            break
    if not file_path:
        return f"错误：未找到文件 {file_name}"

    # ---- 2. 读取 ----
    try:
        if file_path.endswith(".csv"):
            df = pd.read_csv(file_path)
        elif file_path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_path)
        else:
            return f"错误：不支持的文件格式"
    except Exception as e:
        return f"读取文件失败: {e}"

    # ---- 3. 解析 filter_json ----
    try:
        filters = json.loads(filter_json) if filter_json and filter_json.strip() else {}
    except Exception as e:
        return f"filter_json 解析失败: {e}"

    # ---- 4. 找到日期列（数据驱动：可被 pd.to_datetime 解析的列） ----
    date_col = None
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        sample = df[col].dropna().head(min(50, len(df)))
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            valid = parsed.dropna()
            if len(valid) / len(sample) > 0.8 and valid.dt.date.nunique() > 1:
                date_col = col
                break
        except Exception:
            continue

    # ---- 5. 应用过滤 ----
    if date_col and any(k in filters for k in ["year", "month", "quarter", "start_date", "end_date"]):
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
        if "year" in filters:
            df = df[df[date_col].dt.year == int(filters["year"])]
        if "month" in filters:
            df = df[df[date_col].dt.month == int(filters["month"])]
        if "quarter" in filters:
            df = df[df[date_col].dt.quarter == int(filters["quarter"])]
        if "start_date" in filters:
            df = df[df[date_col] >= pd.to_datetime(filters["start_date"])]
        if "end_date" in filters:
            df = df[df[date_col] <= pd.to_datetime(filters["end_date"])]

    # 分类列直接过滤
    for key, value in filters.items():
        if key in ["year", "month", "quarter", "start_date", "end_date"]:
            continue
        if key in df.columns:
            df = df[df[key].astype(str) == str(value)]

    if len(df) == 0:
        return "未找到符合条件的数据"

    # ---- 6. 聚合 ----
    if agg_column not in df.columns:
        return f"错误：列 {agg_column} 不存在"

    agg_map = {
        "sum": lambda s: round(float(s.sum()), 2),
        "avg": lambda s: round(float(s.mean()), 2),
        "count": lambda s: int(s.count()),
        "max": lambda s: round(float(s.max()), 2),
        "min": lambda s: round(float(s.min()), 2),
    }
    if agg_func not in agg_map:
        return f"错误：不支持的聚合函数 {agg_func}"

    # ---- 7. 分组或整体 ----
    filter_desc = ",".join(f"{k}={v}" for k, v in filters.items()) or "无过滤"
# 在 common/tools.py 的 aggregate 函数中，替换分组逻辑
    if group_by:
        is_time_group = False
        time_group_label = ""
        if group_by in ["month", "year", "quarter"]:
            if not date_col:
                return f"错误：文件中未找到可识别的日期列，无法按 {group_by} 分组"
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
            if group_by == "month":
                df["_time_group_"] = df[date_col].dt.month
                time_group_label = "month"
            elif group_by == "year":
                df["_time_group_"] = df[date_col].dt.year
                time_group_label = "year"
            elif group_by == "quarter":
                df["_time_group_"] = df[date_col].dt.quarter
                time_group_label = "quarter"
            group_by = "_time_group_"
            is_time_group = True
        elif group_by not in df.columns:
            return f"错误：分组列 {group_by} 不存在"

        grouped = df.groupby(group_by)[agg_column].apply(agg_map[agg_func])
        
        if is_time_group:
            # 【核心修复】补齐缺失的时间维度，防止 LLM 因数据缺失而编造
            if time_group_label == "month":
                grouped = grouped.reindex(range(1, 13), fill_value=0)
                month_map = {1:"1月", 2:"2月", 3:"3月", 4:"4月", 5:"5月", 6:"6月", 7:"7月", 8:"8月", 9:"9月", 10:"10月", 11:"11月", 12:"12月"}
                grouped.index = [month_map[m] for m in grouped.index]
            elif time_group_label == "quarter":
                grouped = grouped.reindex(range(1, 5), fill_value=0)
                grouped.index = [f"Q{q}" for q in grouped.index]
            elif time_group_label == "year":
                grouped = grouped.sort_index()
        else:
            grouped = grouped.sort_values(ascending=False)

        lines = [f"筛选：{filter_desc}；分组：{time_group_label or group_by}；聚合：{agg_func}({agg_column})"]
        for name, val in grouped.items():
            lines.append(f"- {name}: {val}")
            
        # 【新增】自动生成统计摘要，避免 LLM 自己算均值触发幻觉校验
        try:
            total_count = len(df)
            total_sum = float(df[agg_column].sum()) if agg_column in df.columns else 0
            avg_val = round(total_sum / total_count, 2) if total_count > 0 else 0
            lines.append(f"\n【统计摘要】总记录数: {total_count}，{agg_column}总和: {round(total_sum, 2)}，平均{agg_column}: {avg_val}")
        except Exception:
            pass
        return "\n".join(lines)
    else:
        result_val = agg_map[agg_func](df[agg_column])
        return f"筛选：{filter_desc}；{agg_func}({agg_column}) = {result_val}（{len(df)} 条记录）"

# ==================== 知识库检索工具 ====================
def search_knowledge(query: str, department: str = "", **kwargs) -> str:
    """从企业知识库检索相关文档。"""
    from common.rag_v2 import search_knowledge_v2
    try:
        result = search_knowledge_v2(query, department)
        if isinstance(result, dict):
            text = result.get("context_text", "")
        else:
            text = str(result) if result else ""
        if not text.strip():
            return "企业知识库中未找到相关内容。"
        if len(text) > 30000:
            text = text[:30000] + "\n...（内容过长，已截断）"
        return text
    except Exception as e:
        return f"知识库检索失败: {e}"

# ==================== 图像生成 ====================
def generate_image(prompt: str, negative_prompt: str = "") -> str:
    api_key = os.getenv("STABILITY_API_KEY")
    if not api_key:
        return "图像生成未配置（缺少 STABILITY_API_KEY）"
    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    payload = {
        "text_prompts": [{"text": prompt, "weight": 1.0}, {"text": negative_prompt or "blurry, ugly, low quality", "weight": -1.0}],
        "cfg_scale": 7, "samples": 1, "steps": 30, "width": 1024, "height": 1024,
    }
    try:
        resp = _request_with_retry("POST", url, retries=1, json=payload, headers=headers, timeout=30)
        if resp and resp.status_code == 200:
            data = resp.json()
            if "artifacts" in data:
                img_b64 = data["artifacts"][0]["base64"]
                return f"图片已生成（base64）：![生成图片](data:image/png;base64,{img_b64})"
            return f"图像生成失败: {data.get('message', '未知错误')}"
        return f"图像生成失败: 无响应"
    except Exception as e:
        return f"图像生成错误: {e}"

def fetch_webpage(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = _request_with_retry("GET", url, retries=1, headers=headers, timeout=10)
        if not resp:
            return "网页抓取失败: 网络错误"
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:3000]
    except Exception as e:
        return f"网页抓取失败: {e}"

# ==================== OCR ====================
def get_ocr_token() -> str:
    api_key = os.getenv("BAIDU_OCR_API_KEY") or os.getenv("BAIDU_ASR_API_KEY")
    secret_key = os.getenv("BAIDU_OCR_SECRET_KEY") or os.getenv("BAIDU_ASR_SECRET_KEY")
    if not api_key or not secret_key:
        return ""
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key}
    try:
        resp = _request_with_retry("GET", url, retries=1, params=params, timeout=10)
        if resp:
            return resp.json().get("access_token", "")
        return ""
    except Exception:
        return ""

def ocr_image(image_path: str) -> str:
    token = get_ocr_token()
    if not token:
        return "OCR 鉴权失败（缺少百度 OCR 凭据）"
    try:
        with open(image_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"图片读取失败: {e}"
    url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
    payload = {"image": img_base64, "detect_direction": "true", "language_type": "CHN_ENG"}
    params = {"access_token": token}
    try:
        resp = _request_with_retry("POST", url, retries=1, data=payload, params=params, timeout=15)
        if resp:
            data = resp.json()
            if "words_result" in data:
                return "\n".join([item["words"] for item in data["words_result"]])
            return f"OCR 识别失败: {data.get('error_msg', '未知错误')}"
        return "OCR 识别失败: 网络错误"
    except Exception as e:
        return f"OCR 请求错误: {e}"

def recognize_table(image_path: str) -> str:
    token = get_ocr_token()
    if not token:
        return "表格识别未配置或鉴权失败"
    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"图片读取失败: {e}"
    url = "https://aip.baidubce.com/rest/2.0/ocr/v1/table"
    data = {"image": img_b64, "return_excel": "false", "cell_contents": "true"}
    params = {"access_token": token}
    try:
        resp = _request_with_retry("POST", url, retries=1, data=data, params=params, timeout=30)
        if not resp:
            return "表格识别失败: 网络错误"
        result = resp.json()
    except Exception as e:
        return f"表格识别请求失败: {e}"
    if "error_code" in result:
        return f"表格识别失败: {result.get('error_msg', '未知错误')}"
    try:
        tables_data = result.get("tables_result", [])
        if not tables_data:
            return "未识别到表格结构"
        all_tables_text = ""
        for table_idx, table in enumerate(tables_data):
            cells = table.get("body", [])
            if not cells:
                continue
            max_row = max((cell.get("row_end", cell.get("row_start", 0)) for cell in cells), default=0)
            max_col = max((cell.get("col_end", cell.get("col_start", 0)) for cell in cells), default=0)
            grid = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]
            for cell in cells:
                grid[cell.get("row_start", 0)][cell.get("col_start", 0)] = cell.get("words", "")
            cleaned_rows = []
            for row in grid:
                while row and row[-1] == "":
                    row.pop()
                if any(cell != "" for cell in row):
                    cleaned_rows.append(row)
            if not cleaned_rows:
                continue
            table_text = "\n".join([",".join(row) for row in cleaned_rows])
            all_tables_text += f"表格 {table_idx+1}:\n{table_text}\n"
        return all_tables_text if all_tables_text else "未识别到表格内容"
    except Exception as e:
        return f"表格数据解析失败: {e}"

# ==================== 日程管理 ====================
def init_calendar() -> None:
    with sqlite3.connect("calendar.db") as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT, start_time TEXT, end_time TEXT,
                        description TEXT, tenant TEXT DEFAULT 'default')''')
        columns = [row[1] for row in c.execute("PRAGMA table_info(events)")]
        if "tenant" not in columns:
            c.execute("ALTER TABLE events ADD COLUMN tenant TEXT DEFAULT 'default'")
        c.execute("UPDATE events SET tenant = 'default' WHERE tenant IS NULL")
        c.execute("DELETE FROM events WHERE start_time < '2025-01-01'")
        conn.commit()

def add_event(title: str, start_time: str, end_time: str = "", description: str = "", _tenant: str = "default") -> str:
    time_match = re.search(r'(\d{1,2}):(\d{2})', start_time)
    if not time_match:
        return f"添加日程失败: 无法识别时间。实际收到: {start_time}"
    hour, minute = int(time_match.group(1)), int(time_match.group(2))
    if "明天" in start_time:
        target_date = datetime.now() + timedelta(days=1)
    elif "后天" in start_time:
        target_date = datetime.now() + timedelta(days=2)
    else:
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', start_time)
        target_date = datetime.strptime(date_match.group(1), "%Y-%m-%d") if date_match else datetime.now()
    clean_start = f"{target_date.year}-{target_date.month:02d}-{target_date.day:02d} {hour:02d}:{minute:02d}"
    init_calendar()
    try:
        with sqlite3.connect("calendar.db") as conn:
            c = conn.cursor()
            c.execute("SELECT id, title FROM events WHERE start_time = ? AND tenant = ?", (clean_start, _tenant))
            existing = c.fetchone()
            if existing:
                return f"⚠️ 日程冲突！{clean_start} 已存在日程【{existing[1]}】(ID:{existing[0]})。"
            c.execute("INSERT INTO events (title, start_time, end_time, description, tenant) VALUES (?,?,?,?,?)",
                      (title, clean_start, end_time, description, _tenant))
            event_id = c.lastrowid
            conn.commit()
        return f"日程已添加 (ID:{event_id})：{title} 于 {clean_start}"
    except Exception as e:
        return f"添加日程失败: {e}"

def list_events(date: str = "", _tenant: str = "default") -> str:
    init_calendar()
    if date:
        match = re.match(r'(\d{4}-\d{2}-\d{2})', date)
        date = match.group(1) if match else ""
    try:
        with sqlite3.connect("calendar.db") as conn:
            c = conn.cursor()
            if date:
                c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? AND start_time LIKE ? ORDER BY id ASC", (_tenant, date + "%"))
            else:
                c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? ORDER BY id ASC", (_tenant,))
            rows = c.fetchall()
        if not rows:
            return "暂无日程。"
        result = "日程列表：\n"
        for row in rows:
            result += f"ID:{row[0]} | {row[1]} | 开始:{row[2]} | 结束:{row[3]} | {row[4]}\n"
        return result
    except Exception as e:
        return f"查询日程失败: {e}"

def delete_event(event_id: int, _tenant: str = "default") -> str:
    init_calendar()
    try:
        with sqlite3.connect("calendar.db") as conn:
            c = conn.cursor()
            c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE id=? AND tenant=?", (event_id, _tenant))
            row = c.fetchone()
            if not row:
                return f"日程 {event_id} 不存在"
            deleted_event = {"id": row[0], "title": row[1], "start_time": row[2], "end_time": row[3], "description": row[4]}
            c.execute("DELETE FROM events WHERE id=? AND tenant=?", (event_id, _tenant))
            conn.commit()
        return f"日程 {event_id} 已删除。原始数据: {json.dumps(deleted_event)}"
    except Exception as e:
        return f"删除失败: {e}"

# ==================== Saga 补偿函数 ====================
def compensate_add_event(title: str, start_time: str, end_time: str = "", description: str = "", **kwargs):
    match = re.search(r'ID:(\d+)', kwargs.get("result", ""))
    return delete_event(int(match.group(1))) if match else f"无法找到日程ID"

def compensate_send_email(to_email: str, subject: str, body: str, **kwargs):
    try:
        with open("email_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 邮件失败: {to_email}, {subject}\n")
        return f"补偿：邮件发送失败已记录"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_execute_python(code: str, **kwargs):
    try:
        with open("code_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 代码失败:\n{code}\n")
        return "补偿：代码执行错误已记录"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_delete_event(event_id: int, **kwargs):
    match = re.search(r'原始数据: ({.*})', kwargs.get("result", ""))
    if match:
        data = json.loads(match.group(1))
        return add_event(data["title"], data["start_time"], data["end_time"], data["description"])
    return f"补偿：无法恢复日程 {event_id}"

def compensate_generate_image(prompt: str, **kwargs):
    try:
        with open("image_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 图像失败: {prompt}\n")
        return "补偿：图像生成失败已记录"
    except Exception as e:
        return f"补偿记录失败: {e}"

def execute_workflow_tool(name: str, extra_params: Optional[Dict[str, Any]] = None) -> str:
    from common.workflows import execute_workflow
    return execute_workflow(name, extra_params=extra_params)

def generate_chart(
    file_name: str,
    filter_json: str,
    agg_column: str,
    agg_func: str = "sum",
    group_by: str = "month",
    chart_type: str = "line",
    title: str = "",
    **kwargs
) -> str:
    """
    【V3 核心 · Seaborn 版】确定性绘图工具。
    LLM 只需指定文件和过滤条件，后端内部完成：
    读取 -> 过滤 -> 聚合 -> 按时间对齐 -> Seaborn 渲染 -> 返回 Base64 图片 + 统计摘要。
    """
    import pandas as pd
    import numpy as np
    import os, json, io, base64
    import matplotlib
    matplotlib.use('Agg')  # 必须放在 pyplot 之前
    import matplotlib.pyplot as plt
    import seaborn as sns

    # ---------- 1. 定位文件 ----------
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates_dirs = [os.path.join(base, "uploads", "temp"), os.path.join(base, "uploads")]
    file_path = None
    for d in candidates_dirs:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if f == file_name or f.endswith("_" + file_name):
                file_path = os.path.join(d, f)
                break
        if file_path:
            break
    if not file_path:
        return f"错误：未找到文件 {file_name}"

    # ---------- 2. 读取与过滤 ----------
    try:
        if file_path.endswith(".csv"):
            df = pd.read_csv(file_path)
        elif file_path.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_path)
        else:
            return "错误：不支持的文件格式"
    except Exception as e:
        return f"读取文件失败: {e}"

    filters = json.loads(filter_json) if filter_json and filter_json.strip() else {}

    # 数据驱动找日期列
    date_col = None
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        sample = df[col].dropna().head(50)
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            if parsed.dropna().dt.date.nunique() > 1:
                date_col = col
                break
        except Exception:
            continue

    if date_col and any(k in filters for k in ["year", "month", "quarter", "start_date", "end_date"]):
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
        if "year" in filters:
            df = df[df[date_col].dt.year == int(filters["year"])]
        if "month" in filters:
            df = df[df[date_col].dt.month == int(filters["month"])]
        if "quarter" in filters:
            df = df[df[date_col].dt.quarter == int(filters["quarter"])]
        if "start_date" in filters:
            df = df[df[date_col] >= pd.to_datetime(filters["start_date"])]
        if "end_date" in filters:
            df = df[df[date_col] <= pd.to_datetime(filters["end_date"])]

    for key, value in filters.items():
        if key in ["year", "month", "quarter", "start_date", "end_date"]:
            continue
        if key in df.columns:
            df = df[df[key].astype(str) == str(value)]

    if len(df) == 0:
        return "未找到符合条件的数据，无法绘图"

    # ---------- 3. 聚合与对齐 ----------
    if group_by in ["month", "year", "quarter"]:
        if not date_col:
            return "错误：文件中未找到可识别的日期列"
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", format="mixed")
        if group_by == "month":
            df["_time_group_"] = df[date_col].dt.month
        elif group_by == "year":
            df["_time_group_"] = df[date_col].dt.year
        elif group_by == "quarter":
            df["_time_group_"] = df[date_col].dt.quarter
        group_by = "_time_group_"

    agg_map = {
        "sum": lambda s: round(float(s.sum()), 2),
        "avg": lambda s: round(float(s.mean()), 2),
        "count": lambda s: int(s.count()),
    }
    grouped = df.groupby(group_by)[agg_column].apply(agg_map[agg_func])

    if group_by == "_time_group_":
        # 强制补齐 1~12 月，缺失月份填充 0
        grouped = grouped.reindex(range(1, 13), fill_value=0)
        month_map = {1:"1月", 2:"2月", 3:"3月", 4:"4月", 5:"5月", 6:"6月", 7:"7月", 8:"8月", 9:"9月", 10:"10月", 11:"11月", 12:"12月"}
        x_labels = [month_map.get(m, str(m)) for m in grouped.index]
    else:
        grouped = grouped.sort_values(ascending=False)
        x_labels = [str(i) for i in grouped.index]

    y_data = grouped.values.tolist()
    x_data = list(range(len(y_data)))  # 使用数值型 X 轴，避免字符串轴导致的断线问题

    # ---------- 4. Seaborn 渲染 ----------
    # 设置 Seaborn 主题（推荐 whitegrid 或 darkgrid）
    sns.set_theme(style="whitegrid", font="Microsoft YaHei", font_scale=1.1)
    plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

    fig, ax = plt.subplots(figsize=(7, 3.5), dpi=100)

    if chart_type == "bar":
        # 柱状图：使用 viridis 渐变色
        colors = sns.color_palette("viridis", len(x_data))
        bars = ax.bar(x_data, y_data, color=colors, width=0.6)
        # 在柱子上方标注数值
        for bar, val in zip(bars, y_data):
            if val > 0:
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(), f'{val}',
                        ha='center', va='bottom', fontsize=9, color='#333333')
    else:
        # 折线图：使用明亮蓝色，并填充区域
        ax.plot(x_data, y_data, marker='o', markersize=7, linewidth=2.5, color='#2196F3')
        ax.fill_between(x_data, y_data, alpha=0.12, color='#2196F3')
        # 标注数据点
        for x, y in zip(x_data, y_data):
            if y > 0:
                ax.annotate(f'{y}', (x, y), textcoords="offset points", xytext=(0, 10),
                            ha='center', fontsize=9, color='#333333')

    # 设置轴标签
    ax.set_xticks(x_data)
    ax.set_xticklabels(x_labels, fontsize=10)
    ax.set_title(title or f"{agg_func}({agg_column}) 趋势图", fontsize=16, fontweight='bold', pad=15)
    ax.set_xlabel("", fontsize=12)
    ax.set_ylabel(agg_column, fontsize=12)
    sns.despine(left=True, bottom=True)  # 去掉上、右边框，更简洁

    plt.tight_layout()

    # 保存为静态文件（替代 base64）
    import uuid as _uuid
    charts_dir = os.path.join(base, "uploads", "charts")
    os.makedirs(charts_dir, exist_ok=True)
    chart_filename = f"{_uuid.uuid4().hex[:12]}.png"
    chart_path = os.path.join(charts_dir, chart_filename)
    plt.savefig(chart_path, dpi=100, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    chart_url = f"/charts/{chart_filename}"

    # ---------- 5. 统计摘要 ----------
    total_count = len(df)
    total_sum = float(df[agg_column].sum()) if agg_column in df.columns else 0
    avg_val = round(total_sum / total_count, 2) if total_count > 0 else 0
    summary = (
        f"\n\n【统计摘要】总记录数: {total_count}，"
        f"{agg_column}总和: {round(total_sum, 2)}，"
        f"平均{agg_column}: {avg_val}"
    )

    return f"图片已生成：![{title}]({chart_url}){summary}"

COMPENSATIONS = {
    "send_email": compensate_send_email,
    "add_event": compensate_add_event,
    "execute_python": compensate_execute_python,
    "delete_event": compensate_delete_event,
    "generate_image": compensate_generate_image,
}

# ==================== V3 工具元数据 ====================
TOOLS_METADATA = [
    {"type": "function", "function": {"name": "get_current_time", "description": "获取当前日期时间", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "calculator", "description": "数学计算", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "query_database", "description": "查询 SQLite 数据库", "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "发送邮件", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "搜索互联网", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "execute_python", "description": "执行 Python 代码进行计算或数据处理。【重要】此沙箱不支持绘图库，禁止用于画图/绘制图表场景。如需画图，请使用 generate_chart 工具。", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "speech_to_text", "description": "音频转文本", "parameters": {"type": "object", "properties": {"audio_file_path": {"type": "string"}}, "required": ["audio_file_path"]}}},
    {"type": "function", "function": {"name": "analyze_file", "description": "分析上传的 CSV/Excel 文件概况", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}},
    # 【V3 核心】聚合执行器：LLM 决定 file/filter/agg，后端只执行
    {"type": "function", "function": {"name": "aggregate", "description": "对数据文件执行确定性的统计分析。当用户询问趋势、分布、排名、汇总、同比/环比，或任何涉及金额、数量、频次的统计问题时，应调用此工具。支持按年/月/季/自定义区间过滤，支持按分组列或时间维度（month/year/quarter）汇总。", "parameters": {"type": "object", "properties": {
        "file_name": {"type": "string", "description": "数据文件名，如 coffee_sales.csv"},
        "filter_json": {"type": "string", "description": "过滤条件的 JSON 字符串。可选键：year（年）、month（月）、quarter（季）、start_date、end_date，或直接按分类列名过滤，如 {\"coffee_name\": \"Latte\"}。无条件时传 '{}'"},
        "agg_column": {"type": "string", "description": "要聚合的列名，如 price"},
        "agg_func": {"type": "string", "description": "聚合函数：sum/avg/count/max/min", "enum": ["sum", "avg", "count", "max", "min"]},
        "group_by": {"type": "string", "description": "可选分组列名，如 coffee_name。若需按时间维度分组，可传入 'month'、'year' 或 'quarter'。留空则整体聚合"}
    }, "required": ["file_name", "filter_json", "agg_column", "agg_func"]}}},
    {"type": "function", "function": {"name": "generate_chart", "description": "根据数据生成图表。当用户要求画图、绘制折线图/柱状图/饼图、生成可视化图表时，必须调用此工具。此工具会自动完成数据读取、聚合、渲染，返回高质量的彩色图片。禁止使用 execute_python 手写绘图代码。", "parameters": {"type": "object", "properties": {
        "file_name": {"type": "string", "description": "数据文件名，如 coffee_sales.csv"},
        "filter_json": {"type": "string", "description": "过滤条件的JSON字符串。可选键：year, month, quarter, start_date, end_date。无条件时传 '{}'"},
        "agg_column": {"type": "string", "description": "要聚合的列名，如 price"},
        "agg_func": {"type": "string", "description": "聚合函数：sum/avg/count", "enum": ["sum", "avg", "count"]},
        "group_by": {"type": "string", "description": "分组列，画时间趋势图固定传 'month'", "default": "month"},
        "chart_type": {"type": "string", "description": "图表类型：line/bar", "enum": ["line", "bar"], "default": "bar"},
        "title": {"type": "string", "description": "图表标题"}
    }, "required": ["file_name", "filter_json", "agg_column", "agg_func", "group_by", "chart_type"]}}},
    {"type": "function", "function": {"name": "search_knowledge", "description": "从企业知识库检索相关文档。适用于询问企业内部知识、技术文档、FAQ、故障排查等。", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "department": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_webpage", "description": "抓取网页文本", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "generate_image", "description": "生成图片", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "ocr_image", "description": "识别图片文字", "parameters": {"type": "object", "properties": {"image_path": {"type": "string"}}, "required": ["image_path"]}}},
    {"type": "function", "function": {"name": "add_event", "description": "添加日程", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "description": {"type": "string"}}, "required": ["title", "start_time"]}}},
    {"type": "function", "function": {"name": "list_events", "description": "列出日程", "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "delete_event", "description": "删除日程", "parameters": {"type": "object", "properties": {"event_id": {"type": "integer"}}, "required": ["event_id"]}}},
    {"type": "function", "function": {"name": "recognize_table", "description": "识别图片表格", "parameters": {"type": "object", "properties": {"image_path": {"type": "string"}}, "required": ["image_path"]}}},
    {"type": "function", "function": {"name": "execute_workflow", "description": "执行工作流", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
]

# ==================== 工具映射 ====================
AVAILABLE_TOOLS = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "send_email": send_email,
    "web_search": web_search,
    "execute_python": execute_python,
    "speech_to_text": speech_to_text,
    "analyze_file": analyze_file,
    "aggregate": aggregate,
    "search_knowledge": search_knowledge,
    "fetch_webpage": fetch_webpage,
    "generate_image": generate_image,
    "ocr_image": ocr_image,
    "add_event": add_event,
    "list_events": list_events,
    "delete_event": delete_event,
    "recognize_table": recognize_table,
    "execute_workflow": execute_workflow_tool,
    "generate_chart": generate_chart,
}