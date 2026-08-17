# bus_memory/app.py
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gradio as gr
import asyncio
import json
import pandas as pd
from common.main import chat_core, set_workers, simple_log_tool
from common.memory import memory
from common.tools import (
    get_current_time, calculator,
    query_database, web_search, execute_python,
    speech_to_text, analyze_file,
    fetch_webpage, generate_image,
    ocr_image, add_event, list_events, delete_event,
    recognize_table, send_email, init_calendar,
    execute_workflow_tool,
)
from bus_memory.event_bus import EventBus
from common.agents_memory import WorkerAgent, QueryWorker
from common.auth import init_users_db, authenticate, get_user_info
from common.workflows import add_workflow, list_workflows

os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")

# ================= 全局资源初始化 =================
bus = EventBus()

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
    "execute_workflow": execute_workflow_tool,
}

query_worker = QueryWorker("QueryWorker", query_worker_tools, bus)
command_worker = WorkerAgent("CommandWorker", command_worker_tools, bus)
TOOL_ROUTER = {}
for name in query_worker.tools:
    TOOL_ROUTER[name] = query_worker
for name in command_worker.tools:
    TOOL_ROUTER[name] = command_worker
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
    """返回所有已知租户列表（当前用户租户已在 memory.all_tenants 中）"""
    return sorted(list(memory.all_tenants))
    

with gr.Blocks(title="AI 智能体") as demo:
    # ---------- 全局状态 ----------
    user_state = gr.State(value=None)
    session_user_input = gr.Textbox(visible=False)
    last_user_message = gr.State("")
    last_assistant_message = gr.State("")
    feedback_up = gr.State("up")
    feedback_down = gr.State("down")
    pending_file = gr.State(None)
    attachment_msg = gr.Markdown("", elem_id="attachment-msg")

    # ---------- 登录界面 ----------
    with gr.Column(visible=False) as login_column:
        gr.Markdown("# 🔐 AI 智能体 - 请登录")
        username_input = gr.Textbox(label="用户名（小写）")
        pin_input = gr.Textbox(label="PIN 码", type="password")
        login_btn = gr.Button("登录")
        login_msg = gr.Markdown("")

    # ---------- 主聊天界面 ----------
    with gr.Column(visible=False) as chat_column:
        with gr.Tab("聊天"):
            gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具）")

            with gr.Row():
                tenant_dropdown = gr.Dropdown(
                    choices=get_available_tenants(),
                    value="default",
                    label="当前租户",
                    interactive=False,
                    scale=1
                )
                refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

            with gr.Row():
                user_display = gr.Markdown("")
                logout_btn = gr.Button("退出登录", size="sm")

            chatbot = gr.Chatbot(label="对话", height=500, value=[])

            # ========== 统一输入框容器 ==========
            with gr.Column(elem_id="input-box"):
                # 第一行：上传按钮、附件提示、清除按钮（都靠左）
                with gr.Row(elem_id="attachment-row"):
                    file_upload_btn = gr.UploadButton(
                        "📎 上传文件",
                        file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                        scale=0,
                        elem_id="upload-btn"
                    )
                    attachment_msg = gr.Markdown("", elem_id="attachment-msg")
                    clear_file_btn = gr.Button("❌", scale=0, elem_id="clear-btn", visible=False)

                # 第二行：输入框
                text_input = gr.Textbox(
                    show_label=False,
                    placeholder="发送消息或按住空格说话，松开发送...",
                    scale=4,
                    elem_id="chat-input"
                )

                # 第三行：反馈按钮和提示
                with gr.Row(elem_id="feedback-row"):
                    up_btn = gr.Button("👍 有帮助", scale=0, elem_id="up-btn")
                    down_btn = gr.Button("👎 无帮助", scale=0, elem_id="down-btn")
                    feedback_msg = gr.Markdown("", elem_id="feedback-msg")

            # 隐藏文件输入（语音和粘贴）
            voice_file_input = gr.File(
                visible=True, type="filepath", elem_id="voice-file-input", label=""
            )
            paste_file_input = gr.File(
                visible=True, type="filepath", elem_id="paste-file-input", label=""
            )

        # ---------- 其他 Tab ----------
        with gr.Tab("系统健康"):
            gr.Markdown("## 🏥 系统健康仪表板")
            health_refresh_btn = gr.Button("刷新数据")
            health_summary_md = gr.Markdown("加载中...")
            health_tool_table = gr.Dataframe(
                headers=["工具名称", "调用次数"],
                interactive=False
            )

        with gr.Tab("Worker 监控"):
            gr.Markdown("## 实时 Worker 状态")
            refresh_btn2 = gr.Button("刷新")
            status_table = gr.Dataframe(
                headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度", "平均耗时(s)", "错误率"],
                interactive=False
            )

        with gr.Tab("工作流管理", visible=False) as workflow_tab:
            gr.Markdown("## 🧩 低代码工作流配置")
            gr.Markdown("仅管理员可配置。定义工作流后，在聊天中可说“执行工作流 xxx”来调用。")
            workflow_name_input = gr.Textbox(label="工作流名称")
            workflow_desc_input = gr.Textbox(label="描述")
            workflow_steps_input = gr.Textbox(
                label="步骤 JSON",
                placeholder='[{"tool": "get_current_time", "arguments": {}}, {"tool": "web_search", "arguments": {"query": "今日新闻"}}]'
            )
            workflow_create_btn = gr.Button("创建工作流")
            workflow_create_msg = gr.Markdown("")

            with gr.Row():
                refresh_workflow_btn = gr.Button("刷新列表")
            workflow_list = gr.Dataframe(
                headers=["名称", "描述", "创建者", "创建时间"],
                interactive=False
            )

            def create_workflow(name, desc, steps_json, user):
                if not user or user.get("role") != "admin":
                    return "❌ 只有管理员可以创建工作流。"
                try:
                    steps = json.loads(steps_json)
                    if not isinstance(steps, list):
                        return "❌ 步骤必须是 JSON 数组。"
                    ok = add_workflow(name, desc, steps, user["username"])
                    if ok:
                        updated_list = refresh_workflows()
                        return f"✅ 工作流 {name} 已创建。", updated_list
                    else:
                        return "❌ 工作流名称已存在。", refresh_workflows()
                except Exception as e:
                    return f"❌ 步骤 JSON 解析失败: {e}", refresh_workflows()

            def refresh_workflows():
                workflows = list_workflows()
                if workflows:
                    df = pd.DataFrame(workflows, columns=["名称", "描述", "创建者", "创建时间"])
                else:
                    df = pd.DataFrame(columns=["名称", "描述", "创建者", "创建时间"])
                return df

            workflow_create_btn.click(
                fn=create_workflow,
                inputs=[workflow_name_input, workflow_desc_input, workflow_steps_input, user_state],
                outputs=[workflow_create_msg, workflow_list]
            )

            refresh_workflow_btn.click(
                fn=refresh_workflows,
                inputs=[],
                outputs=[workflow_list]
            )

            workflow_list.value = refresh_workflows()

    # ================= 登录、退出、加载函数 =================
    def login(username, pin):
        user = authenticate(username.strip().lower(), pin)
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
                f"✅ 登录成功，欢迎 {user['display_name']}！",
                f"**当前用户：{user['display_name']} ({user['department']} - {user['position']})**",
                gr.update(visible=(user.get("role") == "admin"))
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
                gr.update(visible=False)
            )

    login_btn.click(
        fn=login,
        inputs=[username_input, pin_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab]
    )

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
            gr.update(visible=False)
        )

    logout_btn.click(
        fn=logout,
        inputs=[],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab]
    )

    def load_history(session_username):
        if session_username:
            user = get_user_info(session_username)
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
                    gr.update(visible=(user.get("role") == "admin"))
                )
        return (
            None,
            gr.update(visible=True),
            gr.update(visible=False),
            [],
            gr.Dropdown(choices=get_available_tenants(), value="default"),
            "",
            "",
            gr.update(visible=False)
        )

    demo.load(
        fn=load_history,
        inputs=[session_user_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab],
        js="() => sessionStorage.getItem('suo_user') || ''"
    )

    # ================= 文件上传暂存 =================
    def handle_file_upload(file):
        if file is None:
            return None, "", gr.update(visible=False)
        file_path = file.name if hasattr(file, 'name') else str(file)
        file_name = os.path.basename(file_path)
        return file_path, f"📎 {file_name}", gr.update(visible=True)

    file_upload_btn.upload(
        fn=handle_file_upload,
        inputs=[file_upload_btn],
        outputs=[pending_file, attachment_msg, clear_file_btn]
    )
    paste_file_input.upload(
        fn=handle_file_upload,
        inputs=[paste_file_input],
        outputs=[pending_file, attachment_msg, clear_file_btn]
    )

    def clear_file():
        return None, "", gr.update(visible=False)

    clear_file_btn.click(
        fn=clear_file,
        inputs=[],
        outputs=[pending_file, attachment_msg, clear_file_btn]
    )

    # ================= 主处理函数 =================
    async def unified_handler(message, history, file, user):
        if not user:
            return history or [], "", None, "", ""

        session_id = user.get("username", "default")
        memory.set_tenant(session_id, user.get("tenant", session_id))

        # 处理 /logs
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
                            logs_display += f"**记录{idx}** | 时间: {timestamp}\n"
                            logs_display += f"用户: {username} | 角色: {role} | 模式: {mode} | 状态: {status}\n"
                            logs_display += f"请求: {user_query[:80]}\n"
                            if mode == "regular":
                                tool = entry.get('tool', '')
                                result = entry.get('result', '')
                                logs_display += f"工具: {tool} | 结果: {result[:60]}\n"
                            else:
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

        # 文件分析
        file_name = None
        if file is not None:
            file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
            ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_result = ""

            if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
                if message and "表格" in message:
                    file_result = await asyncio.to_thread(recognize_table, file_path)
                else:
                    file_result = await asyncio.to_thread(ocr_image, file_path)
            elif ext in ('.csv', '.xlsx', '.xls'):
                file_result = await asyncio.to_thread(analyze_file, file_path)
            elif ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
                file_result = await asyncio.to_thread(speech_to_text, file_path)
            else:
                file_result = "不支持的文件类型"

            file_result = str(file_result)
            memory.set_file_context(session_id, f"【上传文件：{file_name}】\n{file_result}")
            memory.add_uploaded_file(session_id, file_name, file_result)
            simple_log_tool(session_id, file_name, "file_upload", {"file_name": file_name}, "文件上传成功")

            # 音频文件直接转写并回答
            if ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
                history = history or []
                history.append({"role": "user", "content": f"🎤 语音输入：{file_result}"})
                answer = await chat_core(session_id, file_result, query_worker, command_worker, TOOL_ROUTER)
                history.append({"role": "assistant", "content": answer})
                return history, "", None, f"🎤 语音输入：{file_result}", answer

        # 文件就绪提示
        if (not message or not message.strip()) and file_name:
            user_msg = f"📎 上传文件：{file_name}"
            assistant_msg = "文件已就绪，您可以基于该内容提问。"
            history = history or []
            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": assistant_msg})
            return history, "", None, user_msg, assistant_msg

        # 纯文本处理
        if not message or not message.strip():
            return history or [], "", None, "", ""

        display_msg = message
        if file_name:
            display_msg = f"📎 上传文件：{file_name}\n{message}"

        history = history or []
        history.append({"role": "user", "content": display_msg})

        answer = await chat_core(session_id, message, query_worker, command_worker, TOOL_ROUTER)
        history.append({"role": "assistant", "content": answer})
        return history, "", None, display_msg, answer

    # ================= 文本提交 =================
    async def handle_text_with_file(text, history, user_state, pending_file_val):
        result = await unified_handler(text, history, pending_file_val, user_state)
        return (*result, None, "", gr.update(visible=False))

    text_input.submit(
        fn=handle_text_with_file,
        inputs=[text_input, chatbot, user_state, pending_file],
        outputs=[chatbot, text_input, file_upload_btn, last_user_message, last_assistant_message, pending_file, attachment_msg, clear_file_btn]
    )

    # 语音文件上传
    voice_file_input.upload(
        unified_handler,
        [text_input, chatbot, voice_file_input, user_state],
        [chatbot, text_input, voice_file_input, last_user_message, last_assistant_message]
    )

    # 反馈处理
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

    # Worker监控刷新
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

    # 健康仪表板更新
    def update_health_dashboard():
        from common.health import get_system_health
        health = get_system_health()
        summary = f"""
**📊 总体统计**
- 总任务数：{health['total_tasks']}
- 成功任务：{health['success_tasks']} | 失败任务：{health['failed_tasks']}
- 成功率：{health['success_rate']}%
- 活跃用户（24h）：{health['active_users']} | 总用户：{health['total_users']}
- 反馈总数：{health['total_feedback']}（👍 {health['up_feedback']} / 👎 {health['down_feedback']}）
        """
        tool_data = [[tool, count] for tool, count in health['sorted_tools']]
        if not tool_data:
            tool_data = [["暂无数据", 0]]
        tool_df = pd.DataFrame(tool_data, columns=["工具名称", "调用次数"])
        return summary, tool_df

    health_refresh_btn.click(
        fn=update_health_dashboard,
        inputs=[],
        outputs=[health_summary_md, health_tool_table]
    )
    health_refresh_btn.click(fn=update_health_dashboard, inputs=[], outputs=[health_summary_md, health_tool_table])

# ================= 启动入口 =================
if __name__ == "__main__":
    init_users_db()
    init_db()
    init_calendar()
    loop = asyncio.get_event_loop()
    loop.create_task(query_worker.run_loop())
    loop.create_task(command_worker.run_loop())
    port = int(os.environ.get("PORT", 7860))
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        theme=gr.themes.Soft(),
        head=voice_script + paste_script,
        css="""
            #voice-file-input { display: none; }
            #paste-file-input { display: none; }
            #input-box {
                border: 2px solid #ccc;
                border-radius: 12px;
                padding: 8px;
                background: #fafafa;
            }
            #attachment-row {
                display: flex;
                align-items: center;
                gap: 4px;
                margin-bottom: 4px;
            }
            #attachment-msg {
                margin: 0;
                font-size: 0.9em;
                color: #333;
            }
            #clear-btn {
                padding: 0 2px;
                color: #ff5555;
                font-size: 0.9em;
            }
            #feedback-row {
                display: flex;
                align-items: center;
                gap: 4px;
                margin-top: 4px;
            }
            #chat-input textarea {
                border: none !important;
                box-shadow: none !important;
                background: transparent !important;
                padding: 8px 4px;
                font-size: 1em;
            }
            #chat-input textarea::placeholder {
                color: #aaa;
                opacity: 1;
            }
            #chat-input label {
                display: none !important;
            }
        """
    )