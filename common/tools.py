# common/tools.py
"""
智能体工具函数库

包含所有可供智能体调用的工具函数及其元数据。
每个工具函数都设计为同步、可直接执行的，并返回字符串结果。
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
        except (requests.Timeout, requests.ConnectionError) as e:
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
                return "错误：表达式包含不允许的字符，请只使用数字和运算符。"
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
        else:
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
            title = r.get("title", "")
            href = r.get("href", "")
            body = r.get("body", "")
            formatted.append(f"标题: {title}\n链接: {href}\n摘要: {body}\n")
        return "\n".join(formatted)
    except Exception as e:
        return f"搜索失败: {e}"

def execute_python(code: str, **kwargs) -> str:
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
            else:
                return f"语音识别失败: {data.get('err_msg', '未知错误')}"
        else:
            return "语音识别失败: 网络错误"
    except Exception as e:
        return f"语音识别请求错误: {e}"

# ==================== 数据驱动列角色识别（通用） ====================
def _detect_data_columns(df) -> dict:
    """
    数据驱动识别列角色：
    - date_col: 可被 pd.to_datetime 解析为"多日"的列（排除纯时间列如 6:14）
    - price_col: 数值列
    - name_col: 低基数的文本列（分类）
    """
    import pandas as pd
    roles = {"date_col": None, "price_col": None, "name_col": None}

    # 日期列：采样解析，排除纯时间
    for col in df.columns:
        series = df[col].dropna()
        if len(series) == 0:
            continue
        sample = series.head(min(50, len(series)))
        try:
            parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
            valid = parsed.dropna()
            if len(valid) / len(sample) > 0.8 and valid.dt.date.nunique() > 1:
                roles["date_col"] = col
                break
        except Exception:
            continue

    # 数值列：dtype
    num_cols = df.select_dtypes(include="number").columns.tolist()
    if num_cols:
        # 优先选列名含 price/amount/revenue/sales 的
        preferred = None
        for col in num_cols:
            lc = col.lower()
            if any(k in lc for k in ["price", "amount", "revenue", "sales", "价格", "金额", "收入", "销售额"]):
                preferred = col
                break
        roles["price_col"] = preferred or num_cols[0]

    # 分类列：低基数 object 列
    for col in df.columns:
        if col == roles["date_col"] or col == roles["price_col"]:
            continue
        if df[col].dtype == "object":
            nunique = df[col].nunique()
            if 1 < nunique < min(100, len(df) * 0.3):
                roles["name_col"] = col
                break

    return roles

# ==================== 文件分析（保留为兼容函数） ====================
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
            info += "\n⚠️ 文件较大，仅展示前3行和关键信息。\n"
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

# ==================== 通用动态数据分析工具（核心） ====================
def analyze_data(query: str, file_path: str = "", **kwargs) -> str:
    """
    【通用能力】自动分析上传的 CSV/Excel。
    
    【关键设计】
    1. 支持"月份筛选"：从 query 中提取"XX月"或"XXXX年XX月"，精确过滤后聚合
    2. 数据驱动识别列角色，不硬编码列名
    3. 金额统一 round(x, 2)
    4. 路径查找覆盖 uploads/ 和 uploads/temp/ 两个位置
    """
    # ==================== 路径查找 ====================
    if not file_path:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        search_dirs = [
            os.path.join(base, 'uploads', 'temp'),
            os.path.join(base, 'uploads'),
        ]
        for sdir in search_dirs:
            if not os.path.exists(sdir):
                continue
            candidates = [f for f in os.listdir(sdir)
                          if f.lower().endswith(('.csv', '.xlsx', '.xls'))]
            if not candidates:
                continue
            # 优先文件名出现在 query 里
            for f in candidates:
                base_name = f.split('.')[0].lower()
                if base_name in query.lower() or any(
                    token and token in f.lower()
                    for token in re.findall(r'[a-zA-Z_]+', query)
                ):
                    file_path = os.path.join(sdir, f)
                    break
            if not file_path:
                latest = max(candidates, key=lambda f: os.path.getmtime(os.path.join(sdir, f)))
                file_path = os.path.join(sdir, latest)

    if not file_path:
        return "错误：未识别到具体的数据文件路径，请先上传文件。"

    try:
        import pandas as pd
        if file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
        elif file_path.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(file_path)
        else:
            return "不支持的文件格式"

        # 数据驱动识别列角色
        roles = _detect_data_columns(df)
        date_col = roles["date_col"]
        price_col = roles["price_col"]
        name_col = roles["name_col"]

        if not price_col:
            return "文件结构无法自动识别（未找到数值列）。"

        # ========== 1. 月份筛选（优先级最高） ==========
        if date_col and re.search(r'\d{1,2}\s*月', query):
            month_match = re.search(r'(\d{1,2})\s*月', query)
            year_match = re.search(r'(20\d{2})\s*年', query)

            df[date_col] = pd.to_datetime(df[date_col], errors='coerce', format='mixed')
            target_month = int(month_match.group(1))

            if year_match:
                target_year = int(year_match.group(1))
                filtered = df[
                    (df[date_col].dt.year == target_year) &
                    (df[date_col].dt.month == target_month)
                ]
                period_label = f"{target_year}年{target_month}月"
            else:
                filtered = df[df[date_col].dt.month == target_month]
                period_label = f"{target_month}月"

            if len(filtered) == 0:
                return f"数据中未找到 {period_label} 的记录。"

            total = round(float(filtered[price_col].sum()), 2)
            count = len(filtered)
            avg = round(total / count, 2) if count else 0.0

            result = f"筛选条件：{period_label}\n"
            result += f"成交笔数：{count}\n"
            result += f"销售总收入：{total} 元\n"
            result += f"平均客单价：{avg} 元\n"
            if date_col:
                d_min = filtered[date_col].min()
                d_max = filtered[date_col].max()
                if pd.notna(d_min) and pd.notna(d_max):
                    result += f"覆盖日期：{d_min.strftime('%Y/%m/%d')} ~ {d_max.strftime('%Y/%m/%d')}\n"
            return result

        # ========== 2. 按月汇总 ==========
        if date_col and any(k in query for k in ['汇总', '趋势', '按月']):
            df[date_col] = pd.to_datetime(df[date_col], errors='coerce', format='mixed')
            df['_period_'] = df[date_col].dt.strftime('%Y-%m')
            summary = df.groupby('_period_')[price_col].agg(['sum', 'count']).reset_index()
            summary = summary.sort_values('_period_')
            result = "各月销售汇总：\n"
            for _, row in summary.iterrows():
                result += f"- {row['_period_']}: {round(float(row['sum']), 2)} 元（{int(row['count'])} 笔）\n"
            return result

        # ========== 3. 按品类排名 ==========
        if name_col and any(k in query for k in ['品类', '排名', '最', '哪个']):
            summary = df.groupby(name_col)[price_col].agg(['sum', 'count']).reset_index()
            summary = summary.sort_values('sum', ascending=False)
            result = f"各{name_col}销售汇总（降序）：\n"
            for _, row in summary.head(20).iterrows():
                result += f"- {row[name_col]}: {round(float(row['sum']), 2)} 元（{int(row['count'])} 笔）\n"
            return result

        # ========== 4. 默认：返回摘要 ==========
        return (
            f"文件共 {len(df)} 行，列：{', '.join(df.columns)}\n"
            f"识别角色：日期列={date_col}，数值列={price_col}，分类列={name_col}\n"
            f"请明确您的分析需求（如按月、按品类、指定月份）。"
        )
    except Exception as e:
        import traceback
        print(f"###analyze_data 异常### {traceback.format_exc()}")
        return f"数据分析错误: {e}"

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
            else:
                return f"图像生成失败: {data.get('message', '未知错误')}"
        else:
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

# ==================== OCR 文字识别 ====================
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
            else:
                return f"OCR 识别失败: {data.get('error_msg', '未知错误')}"
        else:
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
                r_start = cell.get("row_start", 0)
                c_start = cell.get("col_start", 0)
                grid[r_start][c_start] = cell.get("words", "")
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
                        title TEXT,
                        start_time TEXT,
                        end_time TEXT,
                        description TEXT,
                        tenant TEXT DEFAULT 'default')''')
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
        if date_match:
            target_date = datetime.strptime(date_match.group(1), "%Y-%m-%d")
        else:
            target_date = datetime.now()
    clean_start = f"{target_date.year}-{target_date.month:02d}-{target_date.day:02d} {hour:02d}:{minute:02d}"
    init_calendar()
    try:
        with sqlite3.connect("calendar.db") as conn:
            c = conn.cursor()
            c.execute("SELECT id, title FROM events WHERE start_time = ? AND tenant = ?", (clean_start, _tenant))
            existing = c.fetchone()
            if existing:
                return f"⚠️ 日程冲突！{clean_start} 已存在日程【{existing[1]}】(ID:{existing[0]})。如需强行安排，请先调用 delete_event 删除旧日程。"
            c.execute("INSERT INTO events (title, start_time, end_time, description, tenant) VALUES (?,?,?,?,?)",
                      (title, clean_start, end_time, description, _tenant))
            event_id = c.lastrowid
            conn.commit()
        return f"日程已添加 (ID:{event_id})：{title} 于 {clean_start} (租户:{_tenant})"
    except Exception as e:
        return f"添加日程失败: {e}"

def list_events(date: str = "", _tenant: str = "default") -> str:
    init_calendar()
    if date:
        match = re.match(r'(\d{4}-\d{2}-\d{2})', date)
        if match:
            date = match.group(1)
        else:
            date = ""
    try:
        with sqlite3.connect("calendar.db") as conn:
            c = conn.cursor()
            if date:
                c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? AND start_time LIKE ? ORDER BY id ASC", (_tenant, date + "%"))
            else:
                c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? ORDER BY id ASC", (_tenant,))
            rows = c.fetchall()
        if not rows and date:
            return "查询日期暂无日程。"
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
                return f"日程 {event_id} 不存在或不属于当前租户"
            deleted_event = {"id": row[0], "title": row[1], "start_time": row[2], "end_time": row[3], "description": row[4]}
            c.execute("DELETE FROM events WHERE id=? AND tenant=?", (event_id, _tenant))
            conn.commit()
        return f"日程 {event_id} 已删除。原始数据: {json.dumps(deleted_event)}"
    except Exception as e:
        return f"删除失败: {e}"

# ==================== Saga 补偿函数 ====================
def compensate_add_event(title: str, start_time: str, end_time: str = "", description: str = "", **kwargs):
    result = kwargs.get("result", "")
    match = re.search(r'ID:(\d+)', result)
    if match:
        event_id = int(match.group(1))
        return delete_event(event_id)
    return f"无法找到日程ID，请手动检查「{title}」"

def compensate_send_email(to_email: str, subject: str, body: str, **kwargs):
    try:
        with open("email_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 邮件发送失败: 收件人={to_email}, 主题={subject}\n")
        return f"补偿：邮件发送至 {to_email} 失败，已记录到日志。"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_execute_python(code: str, **kwargs):
    try:
        with open("code_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 代码执行失败:\n{code}\n")
        return "补偿：代码执行错误已记录。"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_delete_event(event_id: int, **kwargs):
    result = kwargs.get("result", "")
    match = re.search(r'原始数据: ({.*})', result)
    if match:
        data = json.loads(match.group(1))
        return add_event(data["title"], data["start_time"], data["end_time"], data["description"])
    return f"补偿：无法恢复日程 {event_id}，原始数据丢失"

def compensate_generate_image(prompt: str, **kwargs):
    try:
        with open("image_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now()}] 图像生成失败: {prompt}\n")
        return "补偿：图像生成失败已记录。"
    except Exception as e:
        return f"补偿记录失败: {e}"

# ================== 低代码工作流执行 ==================
def execute_workflow_tool(name: str, extra_params: Optional[Dict[str, Any]] = None) -> str:
    from common.workflows import execute_workflow
    return execute_workflow(name, extra_params=extra_params)

COMPENSATIONS = {
    "send_email": compensate_send_email,
    "add_event": compensate_add_event,
    "execute_python": compensate_execute_python,
    "delete_event": compensate_delete_event,
    "generate_image": compensate_generate_image,
}

# ==================== 工具元数据 ====================
TOOLS_METADATA = [
    {"type": "function", "function": {"name": "get_current_time", "description": "获取当前的日期和时间", "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "calculator", "description": "执行数学计算", "parameters": {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}}},
    {"type": "function", "function": {"name": "query_database", "description": "查询本地 SQLite 数据库", "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}},
    {"type": "function", "function": {"name": "send_email", "description": "发送邮件", "parameters": {"type": "object", "properties": {"to_email": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, "required": ["to_email", "subject", "body"]}}},
    {"type": "function", "function": {"name": "web_search", "description": "搜索互联网获取信息", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "execute_python", "description": "安全执行 Python 代码并返回输出", "parameters": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}}},
    {"type": "function", "function": {"name": "speech_to_text", "description": "将音频文件转写为文本", "parameters": {"type": "object", "properties": {"audio_file_path": {"type": "string"}}, "required": ["audio_file_path"]}}},
    {"type": "function", "function": {"name": "analyze_file", "description": "分析上传的 CSV 或 Excel 文件", "parameters": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}}},
    {"type": "function", "function": {"name": "analyze_data", "description": "对本次会话中用户上传的数据文件进行动态分析，支持月份筛选、按月汇总、按品类排名。file_path 由后端自动注入，无需提供。", "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "用户的自然语言分析需求"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_webpage", "description": "抓取指定 URL 的网页文本", "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "generate_image", "description": "使用 AI 生成图片", "parameters": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}},
    {"type": "function", "function": {"name": "ocr_image", "description": "识别图片中的文字", "parameters": {"type": "object", "properties": {"image_path": {"type": "string"}}, "required": ["image_path"]}}},
    {"type": "function", "function": {"name": "add_event", "description": "添加一个日程事件", "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "start_time": {"type": "string"}, "end_time": {"type": "string"}, "description": {"type": "string"}}, "required": ["title", "start_time"]}}},
    {"type": "function", "function": {"name": "list_events", "description": "列出日程", "parameters": {"type": "object", "properties": {"date": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {"name": "delete_event", "description": "根据日程ID删除日程", "parameters": {"type": "object", "properties": {"event_id": {"type": "integer"}}, "required": ["event_id"]}}},
    {"type": "function", "function": {"name": "recognize_table", "description": "识别图片中的表格", "parameters": {"type": "object", "properties": {"image_path": {"type": "string"}}, "required": ["image_path"]}}},
    {"type": "function", "function": {"name": "execute_workflow", "description": "执行一个预定义的工作流", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}}
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
    "analyze_data": analyze_data,
    "fetch_webpage": fetch_webpage,
    "generate_image": generate_image,
    "ocr_image": ocr_image,
    "add_event": add_event,
    "list_events": list_events,
    "delete_event": delete_event,
    "recognize_table": recognize_table,
    "execute_workflow": execute_workflow_tool,
}