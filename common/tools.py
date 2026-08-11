# tools.py
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
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+ 内置


# 最近上传的文件路径（用于 analyze_file 自动使用）
last_uploaded_file = None

# ========== 基础工具 ==========

# ---------- 计算器  (calculator) ----------
def calculator(expression: str):
    """安全计算数学表达式，支持单行纯数字或包含换行的数字列表（自动求和）"""
    try:
        # 如果表达式包含换行，尝试提取每行中的数字并求和
        if '\n' in expression:
            lines = expression.strip().split('\n')
            numbers = []
            for line in lines:
                # 提取行中的数字（忽略字母、符号等）
                cleaned = ''.join(c for c in line if c.isdigit() or c in '.-')
                if cleaned:
                    try:
                        numbers.append(float(cleaned))
                    except ValueError:
                        continue
            if not numbers:
                return "错误：表达式中未找到有效数字"
            total = sum(numbers)
            return str(total)
        else:
            # 单行表达式，移除逗号后计算
            expression = expression.replace(",", "")
            allowed_chars = set("0123456789+-*/().% ^")
            if not all(c in allowed_chars for c in expression.replace(" ", "")):
                return "错误：表达式包含不允许的字符，请只使用数字和运算符。"
            result = eval(expression, {"__builtins__": {}})
            return str(result)
    except Exception as e:
        return f"计算出错: {e}"
        
# ---------- 获取当前的日期和时间  (get_current_time) ----------
def get_current_time():
    """返回当前东八区（北京时间）日期和时间"""
    try:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        # 如果 zoneinfo 失败，手动计算 UTC+8
        now = datetime.utcnow() + timedelta(hours=8)
    return now.strftime("%Y-%m-%d %H:%M:%S")
        
# ---------- 查询数据库  (query_database) ----------
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
        
# ---------- 发送邮件  (send_email) ----------
def send_email(to_email: str, subject: str, body: str, **kwargs):
    api_key = os.getenv("MAILGUN_API_KEY")
    domain = os.getenv("MAILGUN_DOMAIN")
    from_email = os.getenv("EMAIL_FROM")
    if not api_key or not domain or not from_email:
        return "错误：邮件服务未配置（Mailgun 凭据缺失）"

    url = f"https://api.mailgun.net/v3/{domain}/messages"
    auth = ("api", api_key)
    data = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "text": body
    }
    try:
        resp = requests.post(url, auth=auth, data=data, timeout=10)
        if resp.status_code == 200:
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
    """使用 Stability AI 生成图片，返回图片的 base64 数据或错误信息"""
    api_key = os.getenv("STABILITY_API_KEY")
    if not api_key:
        return "图像生成未配置（缺少 STABILITY_API_KEY）"

    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json"
    }
    payload = {
        "text_prompts": [
            {"text": prompt, "weight": 1.0},
            {"text": negative_prompt or "blurry, ugly, low quality", "weight": -1.0}
        ],
        "cfg_scale": 7,
        "samples": 1,
        "steps": 30,
        "width": 1024,
        "height": 1024,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and "artifacts" in data:
            # 返回第一张图片的 base64 编码
            img_b64 = data["artifacts"][0]["base64"]
            return f"图片已生成（base64）：![生成图片](data:image/png;base64,{img_b64})"
        else:
            return f"图像生成失败: {data.get('message', '未知错误')}"
    except Exception as e:
        return f"图像生成错误: {e}"

# ---------- 网页抓取 (WebScraperWorker) ----------
def fetch_webpage(url: str) -> str:
    """抓取指定网页的文本内容，返回前 3000 字符"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # 移除脚本和样式
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        return text[:3000]  # 限制长度，避免超出 token 限制
    except Exception as e:
        return f"网页抓取失败: {e}"
        
# ---------- 识别图片文字 (OCRWorker) ----------
def ocr_image(image_path: str) -> str:
    """使用百度文字识别 API 提取图片中的文字"""
    api_key = os.getenv("BAIDU_OCR_API_KEY") or os.getenv("BAIDU_ASR_API_KEY")
    secret_key = os.getenv("BAIDU_OCR_SECRET_KEY") or os.getenv("BAIDU_ASR_SECRET_KEY")
    if not api_key or not secret_key:
        return "OCR 未配置（缺少百度 OCR API Key/Secret Key）"

    # 获取 access_token（可以复用语音识别的 token 函数，但为清晰可单独写或直接调用）
    def get_ocr_token():
        url = "https://aip.baidubce.com/oauth/2.0/token"
        params = {"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key}
        try:
            resp = requests.get(url, params=params, timeout=10)
            return resp.json().get("access_token", "")
        except Exception:
            return ""

    token = get_ocr_token()
    if not token:
        return "OCR 鉴权失败"

    # 读取图片并 base64 编码
    try:
        with open(image_path, "rb") as f:
            img_base64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"图片读取失败: {e}"

    url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
    payload = {
        "image": img_base64,
        "detect_direction": "true",
        "language_type": "CHN_ENG"
    }
    params = {"access_token": token}
    try:
        resp = requests.post(url, data=payload, params=params, timeout=15)
        data = resp.json()
        if "words_result" in data:
            texts = [item["words"] for item in data["words_result"]]
            return "\n".join(texts)
        else:
            return f"OCR 识别失败: {data.get('error_msg', '未知错误')}"
    except Exception as e:
        return f"OCR 请求错误: {e}"

    # 调用通用文字识别接口
    ocr_url = "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    payload = {
        "access_token": access_token,
        "image": img_base64,
        "language_type": "CHN_ENG"  # 中英文混合
    }
    try:
        resp = requests.post(ocr_url, data=payload, headers=headers, timeout=15)
        data = resp.json()
        if data.get("error_code"):
            return f"OCR 失败: {data.get('error_msg')}"
        words_result = data.get("words_result", [])
        if not words_result:
            return "未识别到文字。"
        texts = [item["words"] for item in words_result]
        return "\n".join(texts)
    except Exception as e:
        return f"OCR 请求错误: {e}"

# ---------- 新增日程管理 ----------
import sqlite3
def init_calendar():
    conn = sqlite3.connect("calendar.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT,
                  start_time TEXT,
                  end_time TEXT,
                  description TEXT)''')
    # 添加 tenant 列（如果不存在）
    try:
        c.execute("ALTER TABLE events ADD COLUMN tenant TEXT DEFAULT 'default'")
    except sqlite3.OperationalError:
        pass   # 列已存在，忽略错误
    # 将旧数据的 tenant 设为 default（避免 null）
    c.execute("UPDATE events SET tenant = 'default' WHERE tenant IS NULL")
    conn.commit()
    conn.close()
    
# ---------- 添加事件 ----------
def add_event(title: str, start_time: str, end_time: str = "", description: str = "", _tenant: str = "default") -> str:
    import re
    match = re.search(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})', start_time)
    if not match:
        return f"添加日程失败: start_time 格式错误，实际收到: {start_time}"
    clean_start = match.group(1)
    init_calendar()
    try:
        conn = sqlite3.connect("calendar.db")
        c = conn.cursor()
        c.execute("INSERT INTO events (title, start_time, end_time, description, tenant) VALUES (?,?,?,?,?)",
                  (title, clean_start, end_time, description, _tenant))
        event_id = c.lastrowid
        conn.commit()
        conn.close()
        return f"日程已添加 (ID:{event_id})：{title} 于 {clean_start} (租户:{_tenant})"
    except Exception as e:
        return f"添加日程失败: {e}"
        
# ---------- 列举事件 ----------
def list_events(date: str = "", _tenant: str = "default") -> str:
    init_calendar()
    # 提取标准日期（YYYY-MM-DD），若无法提取则查询全部
    import re
    if date:
        match = re.match(r'(\d{4}-\d{2}-\d{2})', date)
        if match:
            date = match.group(1)   # 提取到的标准日期
        else:
            date = ""                # 无效格式，查询全部
    try:
        conn = sqlite3.connect("calendar.db")
        c = conn.cursor()
        if date:
            c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? AND start_time LIKE ? ORDER BY start_time",
                      (_tenant, date + "%"))
        else:
            c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE tenant=? ORDER BY start_time",
                      (_tenant,))
        rows = c.fetchall()
        conn.close()
        print(f"[list_events] 租户:{_tenant} 查询日期:{date or '全部'} 结果数:{len(rows)}")
        if not rows and date:
            # 指定日期为空，列出最近5条所有日程（按租户过滤）
            conn2 = sqlite3.connect("calendar.db")
            c2 = conn2.cursor()
            c2.execute("SELECT id, title, start_time FROM events WHERE tenant=? ORDER BY start_time DESC LIMIT 5", (_tenant,))
            recent = c2.fetchall()
            conn2.close()
            if recent:
                recent_text = "\n".join([f"ID:{r[0]} {r[1]} @ {r[2]}" for r in recent])
                return f"查询日期 {date} 暂无日程。但系统中有以下最近日程：\n{recent_text}"
            else:
                return "暂无任何日程。"
        if not rows:
            return "暂无日程。"
        result = "日程列表：\n"
        for row in rows:
            result += f"ID:{row[0]} | {row[1]} | 开始:{row[2]} | 结束:{row[3]} | {row[4]}\n"
        return result
    except Exception as e:
        print(f"[list_events] 异常: {e}")
        return f"查询日程失败: {e}"
        
# ---------- 删除事件 ----------
def delete_event(event_id: int, _tenant: str = "default") -> str:
    init_calendar()
    try:
        conn = sqlite3.connect("calendar.db")
        c = conn.cursor()
        # 先查询原数据（用于补偿）
        c.execute("SELECT id, title, start_time, end_time, description FROM events WHERE id=? AND tenant=?", (event_id, _tenant))
        row = c.fetchone()
        if not row:
            return f"日程 {event_id} 不存在或不属于当前租户"
        # 保存原始数据用于补偿
        deleted_event = {
            "id": row[0], "title": row[1], "start_time": row[2],
            "end_time": row[3], "description": row[4]
        }
        c.execute("DELETE FROM events WHERE id=? AND tenant=?", (event_id, _tenant))
        conn.commit()
        conn.close()
        return f"日程 {event_id} 已删除。原始数据: {json.dumps(deleted_event)}"
    except Exception as e:
        return f"删除失败: {e}"

def compensate_delete_event(event_id: int, **kwargs):
    """补偿删除日程：重新插入被删除的日程"""
    result = kwargs.get("result", "")
    try:
        # 从结果中提取原始日程数据
        import re
        match = re.search(r'原始数据: ({.*})', result)
        if match:
            data = json.loads(match.group(1))
            # 重新添加日程
            return add_event(data["title"], data["start_time"], data["end_time"], data["description"])
        else:
            return f"补偿：无法恢复日程 {event_id}，原始数据丢失"
    except Exception as e:
        return f"补偿失败: {e}"
        
        
# ---------- 图片表格识别 ----------
# 获取 OCR Token
def get_ocr_token():
    api_key = os.getenv("BAIDU_OCR_API_KEY") or os.getenv("BAIDU_ASR_API_KEY")
    secret_key = os.getenv("BAIDU_OCR_SECRET_KEY") or os.getenv("BAIDU_ASR_SECRET_KEY")
    if not api_key or not secret_key:
        return ""
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = {"grant_type": "client_credentials", "client_id": api_key, "client_secret": secret_key}
    try:
        resp = requests.get(url, params=params, timeout=10)
        return resp.json().get("access_token", "")
    except Exception:
        return ""
        
# ---------- 识别图片中的表格 ----------
def recognize_table(image_path: str) -> str:
    """使用百度表格文字识别 V2 接口，返回表格文本（按行列重排，去除空列）"""
    token = get_ocr_token()
    if not token:
        return "表格识别未配置或鉴权失败"

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        return f"图片读取失败: {e}"

    url = "https://aip.baidubce.com/rest/2.0/ocr/v1/table"
    data = {
        "image": img_b64,
        "return_excel": "false",
        "cell_contents": "true"
    }
    params = {"access_token": token}
    try:
        resp = requests.post(url, data=data, params=params, timeout=30)
        result = resp.json()
        print("[表格识别] 百度原始响应:", json.dumps(result, ensure_ascii=False)[:800], flush=True)
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

            max_row = 0
            max_col = 0
            for cell in cells:
                r = cell.get("row_end", cell.get("row_start", 0))
                c = cell.get("col_end", cell.get("col_start", 0))
                if r > max_row:
                    max_row = r
                if c > max_col:
                    max_col = c

            grid = [["" for _ in range(max_col + 1)] for _ in range(max_row + 1)]

            for cell in cells:
                r_start = cell.get("row_start", 0)
                c_start = cell.get("col_start", 0)
                words = cell.get("words", "")
                grid[r_start][c_start] = words

            # 清洗行：去除尾部连续空列，忽略全空行
            cleaned_rows = []
            for row in grid:
                while row and row[-1] == "":
                    row.pop()
                if any(cell != "" for cell in row):
                    cleaned_rows.append(row)

            if not cleaned_rows:
                continue

            table_text = ""
            for row in cleaned_rows:
                table_text += ",".join(row) + "\n"

            all_tables_text += f"表格 {table_idx+1}:\n{table_text}\n"

        return all_tables_text if all_tables_text else "未识别到表格内容"
    except Exception as e:
        return f"表格数据解析失败: {e}"
        

# ---------- 视觉理解 (Describle image) ----------
def describe_image(image_path: str) -> str:
    """当前视觉理解服务暂不可用，返回提示"""
    return "视觉理解服务暂不可用，请稍后重试。如需提取文字，可使用 OCR 功能。"
                

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
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "抓取指定 URL 的网页文本内容，返回前 3000 个字符。用于获取网页全文以深入分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要抓取的网页 URL"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "使用 AI 根据文字描述生成一张图片，返回图片链接（base64）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "图片的英文描述（中文可能识别不佳，建议使用英文）"},
                    "negative_prompt": {"type": "string", "description": "可选的负面提示，描述不希望出现的内容"}
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_image",
            "description": "识别并提取图片中的文字，支持中文和英文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "图片文件的本地路径"}
                },
                "required": ["image_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_event",
            "description": "添加一个日程事件，时间格式 YYYY-MM-DD HH:MM",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "事件标题"},
                    "start_time": {"type": "string", "description": "开始时间，如 2025-08-05 14:00"},
                    "end_time": {"type": "string", "description": "结束时间（可选）"},
                    "description": {"type": "string", "description": "事件描述（可选）"}
                },
                "required": ["title", "start_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "列出日程，可指定日期（YYYY-MM-DD）或不填列出所有",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "查询日期，如 2025-08-05"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "根据日程ID删除一个日程",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer", "description": "日程ID"}
                },
                "required": ["event_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recognize_table",
            "description": "识别图片中的表格，返回 CSV 格式的表格内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "图片文件的本地路径"}
                },
                "required": ["image_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "describe_image",
            "description": "描述图片的内容，返回英文描述。",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string", "description": "图片文件的本地路径"}
                },
                "required": ["image_path"]
            }
        }
    }
]


# ===================== Saga 补偿函数（可执行回滚） =====================
def compensate_add_event(title: str, start_time: str, end_time: str = "", description: str = "", **kwargs):
    result = kwargs.get("result", "")
    import re
    match = re.search(r'ID:(\d+)', result)
    if match:
        event_id = int(match.group(1))
        return delete_event(event_id)
    else:
        return f"无法找到日程ID，请手动检查「{title}」"

def compensate_send_email(to_email: str, subject: str, body: str, **kwargs):
    """补偿发送邮件：记录到日志文件，通知用户"""
    import datetime
    try:
        with open("email_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] 邮件发送失败: 收件人={to_email}, 主题={subject}\n")
        return f"补偿：邮件发送至 {to_email} 失败，已记录到日志。"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_execute_python(code: str, **kwargs):
    """补偿代码执行：记录错误日志（不改变状态）"""
    import datetime
    try:
        with open("code_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] 代码执行失败:\n{code}\n")
        return "补偿：代码执行错误已记录。"
    except Exception as e:
        return f"补偿记录失败: {e}"

def compensate_generate_image(prompt: str, **kwargs):
    """图像生成失败补偿：记录日志（生成操作无真正副作用）"""
    import datetime
    try:
        with open("image_failures.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] 图像生成失败: {prompt}\n")
        return "补偿：图像生成失败已记录。"
    except Exception as e:
        return f"补偿记录失败: {e}"

COMPENSATIONS = {
    "send_email": compensate_send_email,
    "add_event": compensate_add_event,
    "execute_python": compensate_execute_python,
    "delete_event": compensate_delete_event,   # 新增
    "generate_image": compensate_generate_image,
}


# ---------- 工具名称到函数的映射 ----------
AVAILABLE_TOOLS = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "send_email": send_email,
    "web_search": web_search,
    "execute_python": execute_python,
    "speech_to_text": speech_to_text,
    "analyze_file": analyze_file,
    "fetch_webpage": fetch_webpage,
    "generate_image": generate_image,
    "ocr_image": ocr_image,
    "add_event": add_event,
    "list_events": list_events,
    "delete_event": delete_event,
    "recognize_table": recognize_table,   
    "describe_image": describe_image,    
}

        


