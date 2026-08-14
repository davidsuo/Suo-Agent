# bus_memory/app.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gradio as gr
import asyncio
import json
import pandas as pd
from common.main import chat_core, set_workers
from common.memory import memory
from common.tools import (
    get_current_time, calculator,
    query_database, web_search, execute_python,
    speech_to_text, analyze_file,
    fetch_webpage, generate_image,
    ocr_image, add_event, list_events, delete_event,
    recognize_table, send_email, init_calendar,
)
from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker

from common.auth import init_users_db, authenticate, get_user_info  # 确保导入 authenticate 和 init_users_db 以及 filter_tools_by_role

os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

session_id = "render_user"

bus = EventBus()

# Worker 工具集
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
command_worker_tools = {
    "send_email": send_email,
    "add_event": add_event,
    "delete_event": delete_event,
    "execute_python": execute_python,
    "generate_image": generate_image,
}

query_worker = QueryWorker("QueryWorker", query_worker_tools, bus)
command_worker = WorkerAgent("CommandWorker", command_worker_tools, bus)
TOOL_ROUTER = {}
for name in query_worker.tools: TOOL_ROUTER[name] = query_worker
for name in command_worker.tools: TOOL_ROUTER[name] = command_worker
set_workers(query_worker, command_worker, TOOL_ROUTER)

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
    tenants = set(memory.all_tenants)
    current = memory.get_tenant(session_id)
    tenants.add(current)  # 确保当前租户在选项中
    return sorted(list(tenants))

async def unified_handler(message, history, file, user):
    if not user:
        return history, "", None

    session_id = user.get("username", "default")
    # 设置租户（确保与登录时一致）
    memory.set_tenant(session_id, user.get("tenant", session_id))

    loaded = memory.get_history(session_id)
    if loaded:
        if not history or len(history) < len(loaded):
            history = loaded
    if not history:
        history = []

    # 处理 /logs 命令
    if message and message.strip().lower() == "/logs":
        try:
            # ... 原有 /logs 逻辑，使用 session_id ...
            pass
        except FileNotFoundError:
            answer = "暂无规划日志文件。"
        except Exception as e:
            answer = f"读取日志失败: {e}"
        history.append({"role": "user", "content": "/logs"})
        history.append({"role": "assistant", "content": answer})
        return history, "", None

    # 拦截非法 #tenant 命令
    if message and message.strip().startswith("#tenant"):
        answer = "⚠️ 租户切换已由登录系统自动管理，无法手动更改。"
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": answer})
        return history, "", None

    # 文件处理（使用 session_id）
    if file is not None:
        file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
        ext = os.path.splitext(file_path)[1].lower()
        file_name = os.path.basename(file_path)
        loop = asyncio.get_event_loop()
        file_result = ""

        # 异步分析
        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
            if message and "表格" in message:
                file_result = await asyncio.to_thread(recognize_table, file_path)
            else:
                file_result = await asyncio.to_thread(ocr_image, file_path)
        elif ext in ('.csv', '.xlsx', '.xls'):
            file_result = await asyncio.to_thread(analyze_file, file_path)
        elif ext in ('.wav', '.mp3', '.m4a', '.ogg'):
            file_result = await asyncio.to_thread(speech_to_text, file_path)
        else:
            file_result = "不支持的文件类型"
        file_result = str(file_result)

        if ext in ('.wav', '.mp3', '.m4a', '.ogg'):
            history.append({"role": "user", "content": f"🎤 语音输入：{file_result}"})
            answer = await chat_core(session_id, file_result, query_worker, command_worker, TOOL_ROUTER)
            history.append({"role": "assistant", "content": answer})
            memory.append(session_id, file_result, answer)
            return history, "", None
        else:
            memory.append(session_id, f"【上传文件：{file_name}】\n{file_result}", "")
            history.append({"role": "user", "content": f"📎 上传文件：{file_name}"})
            history.append({"role": "assistant", "content": "文件已就绪，您可以基于该内容提问。"})
            return history, "", None

    # 纯文本处理
    if not message or not message.strip():
        return history, "", None

    display_msg = message
    history.append({"role": "user", "content": display_msg})

    answer = await chat_core(session_id, message, query_worker, command_worker, TOOL_ROUTER)
    history.append({"role": "assistant", "content": answer})
    return history, "", None, message, answer   # 多返回两个状态值


def on_tenant_change(new_tenant):
    try:
        if new_tenant:
            current = memory.get_tenant(session_id)
            if new_tenant != current:
                memory.set_tenant(session_id, current)
                memory.set_tenant(session_id, new_tenant)
                loaded_history = memory.get_history(session_id)
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


with gr.Blocks(title="AI 智能体") as demo:
    # ================= 全局状态 =================
    user_state = gr.State(value=None)          # 当前登录用户信息
    browser_user = gr.BrowserState()           # 浏览器级用户名持久化
    last_user_message = gr.State("")           # 最近一次用户问题
    last_assistant_message = gr.State("")      # 最近一次助手回复
    feedback_up = gr.State("up")               # 好评状态
    feedback_down = gr.State("down")           # 差评状态

    # ================= 登录界面 =================
    with gr.Column(visible=False) as login_column:
        gr.Markdown("# 🔐 AI 智能体 - 请登录")
        username_input = gr.Textbox(label="用户名（小写）")
        pin_input = gr.Textbox(label="PIN 码", type="password")
        login_btn = gr.Button("登录")
        login_msg = gr.Markdown("")

    # ================= 主聊天界面 =================
    with gr.Column(visible=False) as chat_column:
        with gr.Tab("聊天"):
            gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具）")

            with gr.Row():
                tenant_dropdown = gr.Dropdown(
                    choices=get_available_tenants(),
                    value=memory.get_tenant(session_id),
                    label="当前租户",
                    interactive=False,          # 禁止手动切换
                    scale=1
                )
                refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

            # 用户信息与退出按钮
            with gr.Row():
                user_display = gr.Markdown("")
                logout_btn = gr.Button("退出登录", size="sm")

            chatbot = gr.Chatbot(label="对话", height=500, value=[])

            with gr.Row():
                text_input = gr.Textbox(
                    label="输入文字（可用 /logs 查看日志）",
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

            # 反馈按钮行
            with gr.Row():
                up_btn = gr.Button("👍 有帮助")
                down_btn = gr.Button("👎 无帮助")
                feedback_msg = gr.Markdown("")

        with gr.Tab("Worker 监控"):
            gr.Markdown("## 实时 Worker 状态")
            refresh_btn2 = gr.Button("刷新")
            status_table = gr.Dataframe(
                headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度", "平均耗时(s)", "错误率"],
                interactive=False
            )

    # ================= 登录函数 =================
    def login(username, pin):
        user = authenticate(username.strip().lower(), pin)
        if user:
            # 设置当前用户和租户
            session_id = user["username"]
            memory.set_tenant(session_id, user["tenant"])
            memory.set_current_user(user)
            hist = memory.get_history(session_id)
            tenants = get_available_tenants()
            return (
                user,
                gr.update(visible=False),          # 隐藏登录框
                gr.update(visible=True),           # 显示聊天框
                hist if hist else [],
                gr.Dropdown(choices=tenants, value=user["tenant"]),
                f"✅ 登录成功，欢迎 {user['display_name']}！",
                f"**当前用户：{user['display_name']} ({user['department']} - {user['position']})**",
                user["username"]                   # 保存到 browser_user
            )
        else:
            return (
                None,
                gr.update(visible=True),
                gr.update(visible=False),
                [],
                gr.update(),
                "❌ 用户名或 PIN 码错误",
                "",
                ""                                # 清空 browser_user
            )

    login_btn.click(
        fn=login,
        inputs=[username_input, pin_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, browser_user]
    )

    # ================= 退出函数 =================
    def logout():
        memory.set_current_user(None)
        return (
            None,
            gr.update(visible=True),
            gr.update(visible=False),
            gr.update(),
            gr.update(),
            "",
            "",
            ""                                # 清空 browser_user
        )

    logout_btn.click(
        fn=logout,
        inputs=[],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, browser_user]
    )

    # ================= 页面加载自动恢复登录 =================
    def load_history(browser_username):
        if browser_username:
            user = get_user_info(browser_username)
            if user:
                session_id = user["username"]
                memory.set_tenant(session_id, user["tenant"])
                memory.set_current_user(user)
                hist = memory.get_history(session_id)
                tenants = get_available_tenants()
                return (
                    user,
                    gr.update(visible=False),
                    gr.update(visible=True),
                    hist if hist else [],
                    gr.Dropdown(choices=tenants, value=user["tenant"]),
                    "",
                    f"**当前用户：{user['display_name']} ({user['department']} - {user['position']})**",
                    browser_username
                )
        # 未登录状态
        return (
            None,
            gr.update(visible=True),
            gr.update(visible=False),
            [],
            gr.Dropdown(choices=get_available_tenants(), value="default"),
            "",
            "",
            ""
        )

    demo.load(
        fn=load_history,
        inputs=[browser_user],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, browser_user]
    )

    # ================= 主处理函数（文本、文件、音频） =================
    async def unified_handler(message, history, file, user):
        if not user:
            return history or [], "", None, "", ""

        session_id = user.get("username", "default")
        memory.set_tenant(session_id, user.get("tenant", session_id))

        # 处理 /logs 命令
        if message and message.strip().lower() == "/logs":
            try:
                if os.path.exists("plan_log.json"):
                    with open("plan_log.json", "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    valid_entries = []
                    for line in lines:
                        try:
                            entry = json.loads(line.strip())
                            if isinstance(entry, dict):
                                valid_entries.append(entry)
                        except json.JSONDecodeError:
                            continue
                    if not valid_entries:
                        answer = "暂无规划日志。"
                    else:
                        recent = valid_entries[-5:] if len(valid_entries) > 5 else valid_entries
                        logs_display = "**📋 最近操作日志（审计）**\n\n"
                        for idx, entry in enumerate(recent, 1):
                            timestamp = entry.get('timestamp', '未知')
                            username = entry.get('username', '未知')
                            role = entry.get('role', '未知')
                            mode = entry.get('mode', '规划')
                            status = entry.get('status', entry.get('final_status', '未知'))
                            user_query = entry.get('user_query', '')
                            # 显示简要信息
                            logs_display += f"**记录{idx}** | 时间: {timestamp}\n"
                            logs_display += f"用户: {username} | 角色: {role} | 模式: {mode} | 状态: {status}\n"
                            logs_display += f"请求: {user_query[:80]}\n"
                            # 如果是常规模式，显示工具和结果摘要
                            if mode == "regular":
                                tool = entry.get('tool', '')
                                result = entry.get('result', '')
                                logs_display += f"工具: {tool} | 结果: {result[:60]}\n"
                            else:
                                # 规划模式显示步骤数
                                plan = entry.get('plan', [])
                                logs_display += f"步骤数: {len(plan)}\n"
                            logs_display += "\n"
                        answer = logs_display
                else:
                    answer = "暂无规划日志文件。"
            except Exception as e:
                answer = f"读取日志失败: {e}"
            history = history or []
            history.append({"role": "user", "content": "/logs"})
            history.append({"role": "assistant", "content": answer})
            return history, "", None, "", ""

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
                answer = await chat_core(session_id, file_result, query_worker, command_worker, TOOL_ROUTER)
                history.append({"role": "assistant", "content": answer})
                memory.append(session_id, file_result, answer)
                return history, "", None, file_result, answer
            else:
                memory.append(session_id, f"【上传文件：{file_name}】\n{file_result}", "")
                history.append({"role": "user", "content": f"📎 上传文件：{file_name}"})
                history.append({"role": "assistant", "content": "文件已就绪，您可以基于该内容提问。"})
                return history, "", None, "", ""

        # 普通文本处理
        if not message or not message.strip():
            return history or [], "", None, "", ""

        display_msg = message
        history = history or []
        history.append({"role": "user", "content": display_msg})

        answer = await chat_core(session_id, message, query_worker, command_worker, TOOL_ROUTER)
        history.append({"role": "assistant", "content": answer})
        memory.append(session_id, message, answer)
        return history, "", None, message, answer

    # 绑定文本、文件、音频事件
    text_input.submit(
        unified_handler,
        [text_input, chatbot, file_upload_btn, user_state],
        [chatbot, text_input, file_upload_btn, last_user_message, last_assistant_message]
    )
    file_upload_btn.upload(
        unified_handler,
        [text_input, chatbot, file_upload_btn, user_state],
        [chatbot, text_input, file_upload_btn, last_user_message, last_assistant_message]
    )
    audio_input_btn.stop_recording(
        unified_handler,
        [text_input, chatbot, audio_input_btn, user_state],
        [chatbot, text_input, audio_input_btn, last_user_message, last_assistant_message]
    )

    # ================= 反馈处理 =================
    async def handle_feedback(feedback, user_msg_state, assistant_msg_state, user_state):
        print(f"[反馈按钮] 触发，feedback={feedback}, user={user_state}, user_msg={user_msg_state[:30]}...", flush=True)
        if not user_state:
            return "⚠️ 请先登录。"
        if not user_msg_state or not assistant_msg_state:
            return "⚠️ 暂无可以评价的对话。"
        try:
            from common.feedback import save_feedback
            save_feedback(user_state["username"], user_msg_state, assistant_msg_state, feedback)
            return f"感谢您的反馈！({feedback})"
        except Exception as e:
            print(f"[反馈错误] {e}", flush=True)
            return f"反馈保存失败: {e}"

    up_btn.click(
        fn=handle_feedback,
        inputs=[feedback_up, last_user_message, last_assistant_message, user_state],
        outputs=[feedback_msg]
    )
    down_btn.click(
        fn=handle_feedback,
        inputs=[feedback_down, last_user_message, last_assistant_message, user_state],
        outputs=[feedback_msg]
    )

    # ================= Worker 监控刷新 =================
    def refresh_status():
        workers = [query_worker, command_worker]
        data = []
        for w in workers:
            stats = w.get_stats()
            data.append([
                stats["name"],
                str(stats["is_running"]),
                stats["task_count"],
                stats["error_count"],
                stats["queue_size"],
                stats["avg_time"],
                stats["error_rate"]
            ])
        return pd.DataFrame(data, columns=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度", "平均耗时(s)", "错误率"])

    refresh_btn2.click(fn=refresh_status, outputs=status_table)
    status_table.value = refresh_status()
           

if __name__ == "__main__":
    init_users_db()
    init_db()
    memory.load_from_file()
    init_calendar()
    loop = asyncio.get_event_loop()
    loop.create_task(query_worker.run_loop())
    loop.create_task(command_worker.run_loop())
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())