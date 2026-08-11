# bus_memory/app.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gradio as gr
import asyncio
import json
import pandas as pd

# 导入公共模块
from common.main import chat_core, set_workers   # 注意：需要从 main.py 导入 set_workers
from common.memory import memory
from common.tools import (
    get_current_time, calculator,
    query_database, web_search, execute_python,
    speech_to_text, analyze_file,
    fetch_webpage, generate_image,
    ocr_image,
    add_event, list_events, delete_event,
    recognize_table,
    send_email,
    init_calendar,   # 确保 tools.py 中存在此函数
)
from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker

# 切换工作目录到项目根目录，以保证 sample.db、plan_log.json 等路径正确
os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

SESSION_ID = "render_user"

# 初始化内存总线
bus = EventBus()

# ========== 查询类 Worker（带缓存） ==========
query_worker_tools = {
    "get_current_time": get_current_time,
    "calculator": calculator,
    "query_database": query_database,
    "list_events": list_events,
    "web_search": web_search,
    "fetch_webpage": fetch_webpage,
    "ocr_image": ocr_image,
    "recognize_table": recognize_table,
    "analyze_file": analyze_file,
    "speech_to_text": speech_to_text,
}
query_worker = QueryWorker("QueryWorker", query_worker_tools, bus)

# ========== 命令类 Worker ==========
command_worker_tools = {
    "send_email": send_email,
    "add_event": add_event,
    "delete_event": delete_event,
    "execute_python": execute_python,
    "generate_image": generate_image,
}
command_worker = WorkerAgent("CommandWorker", command_worker_tools, bus)

# 工具路由表
TOOL_ROUTER = {}
for name in query_worker.tools:
    TOOL_ROUTER[name] = query_worker
for name in command_worker.tools:
    TOOL_ROUTER[name] = command_worker

# 将 Worker 和路由注入 common.main（使 chat_core 能访问）
set_workers(query_worker, command_worker, TOOL_ROUTER)

# ========== 前端逻辑 ==========
def init_db():
    db_path = "sample.db"
    if not os.path.exists(db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY,
                name TEXT,
                position TEXT,
                salary INTEGER
            )
        ''')
        sample_data = [
            (1, "张三", "工程师", 60000),
            (2, "李四", "产品经理", 75000),
            (3, "王五", "设计师", 55000),
            (4, "赵六", "数据分析师", 68000),
        ]
        cursor.executemany("INSERT OR REPLACE INTO employees VALUES (?,?,?,?)", sample_data)
        conn.commit()
        conn.close()
        print("✅ 数据库 sample.db 已自动创建并插入示例数据。")

def get_available_tenants():
    return sorted(list(memory.all_tenants))

async def unified_handler(message, history, file, tenant_dropdown):
    # 特殊命令处理
    if message and message.strip().lower() == "/logs":
        try:
            if os.path.exists("plan_log.json") and os.path.getsize("plan_log.json") > 2 * 1024 * 1024:
                with open("plan_log.json", "r", encoding="utf-8") as f:
                    lines = f.readlines()[-500:]
            else:
                with open("plan_log.json", "r", encoding="utf-8") as f:
                    lines = f.readlines()
            if not lines:
                answer = "暂无规划日志。"
            else:
                recent = lines[-3:] if len(lines) > 3 else lines
                logs_display = "**最近规划日志：**\n\n"
                for idx, line in enumerate(recent, 1):
                    entry = json.loads(line)
                    logs_display += f"记录{idx} | 时间: {entry['timestamp']}\n"
                    logs_display += f"用户需求: {entry['user_query']}\n"
                    logs_display += f"步骤数: {len(entry['plan'])} 步\n"
                    for step_id, result in entry['results'].items():
                        logs_display += f"  → 步骤{step_id}: {str(result)[:100]}...\n"
                    logs_display += "\n"
                answer = logs_display
        except FileNotFoundError:
            answer = "暂无规划日志文件。"
        except Exception as e:
            answer = f"读取日志失败: {e}"
        history = history or []
        history.append({"role": "user", "content": "/logs"})
        history.append({"role": "assistant", "content": answer})
        return history, "", None

    if message and message.strip().startswith("#tenant"):
        try:
            parts = message.strip().split(maxsplit=1)
            if len(parts) == 1:
                current_tenant = memory.get_tenant(SESSION_ID)
                answer = f"当前租户：{current_tenant}"
                history = history or []
                history.append({"role": "user", "content": message})
                history.append({"role": "assistant", "content": answer})
                return history, "", None
            else:
                new_tenant = parts[1].strip()
                current_tenant = memory.get_tenant(SESSION_ID)
                if history:
                    memory.set_tenant(SESSION_ID, current_tenant)
                    memory.set_history(SESSION_ID, history)
                memory.set_tenant(SESSION_ID, new_tenant)
                loaded_history = memory.get_history(SESSION_ID)
                answer = f"已切换到租户：{new_tenant}。"
                if loaded_history:
                    answer += " 已恢复上次会话。"
                    new_history = [{"role": "assistant", "content": answer}] + loaded_history
                else:
                    answer += " 会话已清空。"
                    new_history = [{"role": "assistant", "content": answer}]
                return new_history, "", None
        except Exception as e:
            print(f"[#tenant 处理异常] {e}", flush=True)
            return history or [], f"租户切换失败: {e}", None

    # 文件处理
    if file is not None:
        file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
        ext = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path)
        loop = asyncio.get_event_loop()
        file_result = ""

        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
            if message and "表格" in message:
                file_result = await loop.run_in_executor(None, recognize_table, file_path)
            else:
                file_result = await loop.run_in_executor(None, ocr_image, file_path)
        elif ext in ('.csv', '.xlsx', '.xls'):
            file_result = await loop.run_in_executor(None, analyze_file, file_path)
        elif ext in ('.wav', '.mp3', '.m4a', '.ogg'):
            file_result = await loop.run_in_executor(None, speech_to_text, file_path)
        else:
            file_result = "不支持的文件类型"

        file_result = str(file_result)
        history = history or []

        if ext in ('.wav', '.mp3', '.m4a', '.ogg'):
            history.append({"role": "user", "content": f"🎤 语音输入：{file_result}"})
            answer = await chat_core(SESSION_ID, file_result, query_worker, command_worker, TOOL_ROUTER)
            history.append({"role": "assistant", "content": answer})
            memory.append(SESSION_ID, file_result, answer)
            return history, "", None
        else:
            # 其他文件：暂存内容，显示用户侧上传提示 + 助手就绪回复
            memory.append(SESSION_ID, f"【上传文件：{file_name}】\n{file_result}", "")
            history.append({"role": "user", "content": f"📎 上传文件：{file_name}"})
            history.append({"role": "assistant", "content": "文件已就绪，您可以基于该内容提问。"})
            return history, "", None

    # 纯文本处理
    if not message or not message.strip():
        return history, "", None

    display_msg = message
    history = history or []
    history.append({"role": "user", "content": display_msg})

    answer = await chat_core(SESSION_ID, message, query_worker, command_worker, TOOL_ROUTER)
    history.append({"role": "assistant", "content": answer})
    return history, "", None


def on_tenant_change(new_tenant):
    try:
        if new_tenant:
            current = memory.get_tenant(SESSION_ID)
            if new_tenant != current:
                memory.set_tenant(SESSION_ID, current)
                memory.set_tenant(SESSION_ID, new_tenant)
                loaded_history = memory.get_history(SESSION_ID)
                tenants = get_available_tenants()
                if loaded_history:
                    return loaded_history, gr.Dropdown(choices=tenants, value=new_tenant)
                else:
                    return [], gr.Dropdown(choices=tenants, value=new_tenant)
        return gr.update(), gr.update()
    except Exception as e:
        print(f"[租户切换异常] {e}", flush=True)
        tenants = get_available_tenants()
        return [], gr.Dropdown(choices=tenants, value="default")


# Gradio 界面
with gr.Blocks(title="AI 智能体") as demo:
    with gr.Tab("聊天"):
        gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具）")

        with gr.Row():
            tenant_dropdown = gr.Dropdown(
                choices=get_available_tenants(),
                value="default",
                label="租户切换",
                interactive=True,
                scale=1
            )
            refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

        chatbot = gr.Chatbot(
            label="对话",
            height=500,
            value=memory.get_history(SESSION_ID)   # 加载上次历史
        )

        with gr.Row():
            text_input = gr.Textbox(
                label="输入文字（可用 /logs 查看日志，#tenant 切换租户）",
                placeholder="在这里输入问题或指令...",
                scale=4
            )

        with gr.Row():
            file_upload_btn = gr.UploadButton(
                "📁 上传文件",
                file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                scale=0
            )
            audio_input_btn = gr.Audio(
                label="🎤 语音输入",
                sources=["microphone"],
                type="filepath",
                scale=1
            )

        text_input.submit(
            unified_handler,
            [text_input, chatbot, file_upload_btn, tenant_dropdown],
            [chatbot, text_input, file_upload_btn]
        )

        file_upload_btn.upload(
            unified_handler,
            [text_input, chatbot, file_upload_btn, tenant_dropdown],
            [chatbot, text_input, file_upload_btn]
        )

        audio_input_btn.stop_recording(
            unified_handler,
            [text_input, chatbot, audio_input_btn, tenant_dropdown],
            [chatbot, text_input, audio_input_btn]
        )

        tenant_dropdown.change(
            on_tenant_change,
            [tenant_dropdown],
            [chatbot, tenant_dropdown]
        )

        def refresh_tenants():
            tenants = get_available_tenants()
            return gr.Dropdown(choices=tenants, value=memory.get_tenant(SESSION_ID))
        refresh_btn.click(refresh_tenants, None, tenant_dropdown)

    with gr.Tab("Worker 监控"):
        gr.Markdown("## 实时 Worker 状态")
        refresh_btn2 = gr.Button("刷新")
        status_table = gr.Dataframe(
            headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度"],
            interactive=False
        )

        def refresh_status():
            workers = [query_worker, command_worker]
            data = []
            for w in workers:
                stats = w.get_stats()
                data.append([stats["name"], str(stats["is_running"]), stats["task_count"], stats["error_count"], stats["queue_size"]])
            return pd.DataFrame(data, columns=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度"])

        refresh_btn2.click(fn=refresh_status, outputs=status_table)
        status_table.value = refresh_status()

if __name__ == "__main__":
    init_db()
    init_calendar()   # 确保日程表存在
    loop = asyncio.get_event_loop()
    loop.create_task(query_worker.run_loop())
    loop.create_task(command_worker.run_loop())
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())