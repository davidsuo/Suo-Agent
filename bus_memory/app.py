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


voice_script = """
<script>
(function() {
    let mediaRecorder;
    let audioChunks = [];
    let isRecording = false;

    const statusDiv = document.createElement('div');
    statusDiv.id = 'recording-status';
    statusDiv.style.cssText = 'position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #333; color: #fff; padding: 8px 16px; border-radius: 20px; display: none; z-index: 9999;';
    document.body.appendChild(statusDiv);

    function showStatus(text) { statusDiv.textContent = text; statusDiv.style.display = 'block'; }
    function hideStatus() { statusDiv.style.display = 'none'; }

    document.addEventListener('keydown', async (e) => {
        if (e.code !== 'Space' || isRecording) return;
        const active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA') && active.value && active.value.trim() !== '') return;
        e.preventDefault();
        isRecording = true; audioChunks = []; showStatus('🎤 正在录音...');
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            mediaRecorder.ondataavailable = event => { if (event.data.size > 0) audioChunks.push(event.data); };
            mediaRecorder.onstop = () => {
                hideStatus();
                const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                const file = new File([audioBlob], 'voice_message.webm', { type: audioBlob.type });
                const wrapper = document.getElementById('voice-file-input');
                const fileInput = wrapper ? wrapper.querySelector('input[type="file"]') : null;
                if (fileInput) {
                    const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files;
                    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
                } else { console.error('未找到隐藏的 file input'); }
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
                mediaRecorder = null;
            };
            mediaRecorder.start();
        } catch (err) {
            console.error('录音失败:', err); showStatus('❌ 无法访问麦克风，请检查权限');
            setTimeout(hideStatus, 2000); isRecording = false;
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.code !== 'Space' || !isRecording) return;
        e.preventDefault(); isRecording = false;
        if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
    });
})();
</script>
"""

paste_script = """
<script>
document.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('paste', function(e) {
        const items = (e.clipboardData || e.originalEvent.clipboardData).items;
        for (let i = 0; i < items.length; i++) {
            if (items[i].kind === 'file') {
                const file = items[i].getAsFile();
                const wrapper = document.getElementById('paste-file-input');
                const fileInput = wrapper ? wrapper.querySelector('input[type="file"]') : null;
                if (fileInput) {
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    fileInput.files = dt.files;
                    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
                    e.preventDefault();
                    break;
                }
            }
        }
    });
});
</script>
"""


with gr.Blocks(title="AI 智能体") as demo:
    # ---------- 全局状态 ----------
    user_state = gr.State(value=None)
    session_user_input = gr.Textbox(visible=False)
    pending_file = gr.State(None)
    attachment_html = gr.HTML("", elem_id="attachment-html", scale=0, min_width=0)
    clear_file_btn = gr.Button("❌", scale=0, elem_id="clear-btn", visible=False)

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
                tenant_dropdown = gr.Dropdown(choices=get_available_tenants(), value="default", label="当前租户", interactive=False, scale=1)
                refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

            with gr.Row():
                user_display = gr.Markdown("")
                logout_btn = gr.Button("退出登录", size="sm")

            # ✅ 旧版 3.x 写法：chatbot = gr.Chatbot(label="对话", height=500, value=[])
            # ✅ 新版 4.x 写法：
            #chatbot = gr.Chatbot(label="对话", height=500, value=[], type="messages", show_copy_button=True)
            chatbot = gr.Chatbot(label="对话", height=500, value=[])

            # 上传按钮、文件名、清除按钮（紧密排列）
            with gr.Row(elem_id="upload-row"):   # ✅ 务必删除 equal_width=False
                file_upload_btn = gr.UploadButton(
                    "📎 上传文件",
                    file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                    scale=0, min_width=0   # ✅ scale=0 让它不强行拉伸
                )
                attachment_html = gr.HTML("", elem_id="attachment-html", scale=0, min_width=0) # ✅ 核心：禁止 HTML 抢占空白
                clear_file_btn = gr.Button("❌", scale=0, elem_id="clear-btn", visible=False)

            # 输入框
            with gr.Row(elem_id="input-row"):
                text_input = gr.Textbox(
                    show_label=False,
                    placeholder="发送消息或按住空格说话，松开发送...",
                    scale=4,           # 占据绝大部分宽度
                    interactive=True,
                    autofocus=True,     # 刷新页面后自动聚焦
                    #debounce=0  # ✅ 4.x 完美支持，按回车即发
                )
                send_btn = gr.Button("➡️", scale=0, min_width=0, elem_id="send-btn") # 发送按钮

        # 其他 Tab 保持不变（略）
        with gr.Tab("系统健康"):
            gr.Markdown("## 🏥 系统健康仪表板")
            health_refresh_btn = gr.Button("刷新数据")
            health_summary_md = gr.Markdown("加载中...")
            health_tool_table = gr.Dataframe(headers=["工具名称", "调用次数"], interactive=False)

        with gr.Tab("Worker 监控"):
            gr.Markdown("## 实时 Worker 状态")
            refresh_btn2 = gr.Button("刷新")
            status_table = gr.Dataframe(headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度", "平均耗时(s)", "错误率"], interactive=False)

        with gr.Tab("工作流管理", visible=False) as workflow_tab:
            gr.Markdown("## 🧩 低代码工作流配置")
            gr.Markdown("仅管理员可配置。定义工作流后，在聊天中可说“执行工作流 xxx”来调用。")
            workflow_name_input = gr.Textbox(label="工作流名称")
            workflow_desc_input = gr.Textbox(label="描述")
            workflow_steps_input = gr.Textbox(label="步骤 JSON", placeholder='[{"tool": "get_current_time", "arguments": {}}, {"tool": "web_search", "arguments": {"query": "今日新闻"}}]')
            workflow_create_btn = gr.Button("创建工作流")
            workflow_create_msg = gr.Markdown("")

            with gr.Row():
                refresh_workflow_btn = gr.Button("刷新列表")
            workflow_list = gr.Dataframe(headers=["名称", "描述", "创建者", "创建时间"], interactive=False)

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

            workflow_create_btn.click(fn=create_workflow, inputs=[workflow_name_input, workflow_desc_input, workflow_steps_input, user_state], outputs=[workflow_create_msg, workflow_list])
            refresh_workflow_btn.click(fn=refresh_workflows, inputs=[], outputs=[workflow_list])
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
            return (user, gr.update(visible=False), gr.update(visible=True), hist if hist else [], gr.Dropdown(choices=tenants, value=user["tenant"]), f"✅ 登录成功，欢迎 {user['display_name']}！", f"**当前用户：{user['display_name']} ({user['department']} - {user['position']})**", gr.update(visible=(user.get("role") == "admin")))
        else:
            return (None, gr.update(visible=True), gr.update(visible=False), [], gr.update(), "❌ 用户名或 PIN 码错误", "", gr.update(visible=False))

    login_btn.click(fn=login, inputs=[username_input, pin_input], outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab])

    def logout():
        memory.set_current_user(None)
        return (None, gr.update(visible=True), gr.update(visible=False), gr.update(), gr.update(), "", "", gr.update(visible=False))

    logout_btn.click(fn=logout, inputs=[], outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab])

    def load_history(session_username):
        if session_username:
            user = get_user_info(session_username)
            if user:
                session_id = user["username"]
                memory.set_tenant(session_id, user["tenant"])
                memory.set_current_user(user)
                hist = memory.get_history(session_id)
                tenants = get_available_tenants()
                return (user, gr.update(visible=False), gr.update(visible=True), hist if hist else [], gr.Dropdown(choices=tenants, value=user["tenant"]), "", f"**当前用户：{user['display_name']} ({user['department']} - {user['position']})**", gr.update(visible=(user.get("role") == "admin")))
        return (None, gr.update(visible=True), gr.update(visible=False), [], gr.Dropdown(choices=get_available_tenants(), value="default"), "", "", gr.update(visible=False))

    demo.load(fn=load_history, inputs=[session_user_input], outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab], js="() => sessionStorage.getItem('suo_user') || ''")

    # ================= 文件上传暂存 =================
    def handle_file_upload(file):
        if file is None:
            return None, "", gr.update(visible=False)
        file_path = file.name if hasattr(file, 'name') else str(file)
        file_name = os.path.basename(file_path)
        html = f"<span style='display:inline-block; margin:0; padding:0;'>{file_name}</span>"
        return file_path, html, gr.update(visible=True)

    file_upload_btn.upload(fn=handle_file_upload, inputs=[file_upload_btn], outputs=[pending_file, attachment_html, clear_file_btn])

    def clear_file():
        return None, "", gr.update(visible=False)

    clear_file_btn.click(fn=clear_file, inputs=[], outputs=[pending_file, attachment_html, clear_file_btn])

    # ================= 文本提交（生成器，使用 dict 字典格式兼容当前 Gradio） =================
    # ================= 文本提交（优化后的异步生成器，解决 2 秒延迟） =================
    # ================= 文本提交（优化：修正气泡先后顺序） =================
    # ================= 文本提交（Gradio 3.x 最佳布局适配版） =================
    async def handle_text_with_file_generator(text, history, user_state, pending_file_val):
        history = list(history) if history else []

        # 1. 将“上传文件”和“问题”合并为同一条用户气泡（用换行隔开，视觉整齐）
        user_contents = []
        if pending_file_val:
            file_name = os.path.basename(pending_file_val)
            user_contents.append(f"📎 上传文件：{file_name}")
            memory.set_file_context(user_state.get("username", "default") if user_state else "default", f"【上传文件：{file_name}】\n文件内容待分析")
            
        if text and text.strip():
            user_contents.append(f"💬 {text}")

        if not user_contents:
            user_contents = [" "]

        # 合并成带换行的单条用户消息
        history.append({"role": "user", "content": "\n".join(user_contents)})
        yield history, "", None, "", gr.update(visible=False)

        # 2. AI 气泡先显示“正在分析”（这时用户能立刻看到反馈）
        history.append({"role": "assistant", "content": "⏳ 正在分析文件，请稍候..."})
        yield history, "", None, "", gr.update(visible=False)

        # 3. 调用后台 AI 逻辑（此时不耽误界面渲染）
        session_id = user_state.get("username", "default") if user_state else "default"
        memory.set_tenant(session_id, user_state.get("tenant", session_id) if user_state else session_id)
        
        if text and text.strip():
            answer = await chat_core(session_id, text, query_worker, command_worker, TOOL_ROUTER)
        else:
            answer = "文件已就绪，您可以基于该内容提问。"

        # 4. ⭐ 核心技巧：不追加新气泡，而是**原地更新**上一条 AI 气泡的内容！
        if history and history[-1]["role"] == "assistant":
            # 用 markdown 格式输出，结果非常清晰
            history[-1]["content"] = f"✅ **分析完成！**\n\n{answer}"
        
        yield history, "", None, "", gr.update(visible=False)

    # ================= 事件绑定（优化：按下回车瞬间立刻清空输入框） =================
    # ================= 事件绑定（Gradio 3.x 兼容版：瞬间清空输入框） =================
    # ================= 事件绑定（彻底解决 3.x 丢失 text 输入的坑） =================
    text_input.submit(
        fn=handle_text_with_file_generator,
        inputs=[text_input, chatbot, user_state, pending_file],
        outputs=[chatbot, text_input, pending_file, attachment_html, clear_file_btn],
        show_progress="minimal",
        # ✅ 修正版：接收 inputs 的 4 个参数，清空 UI 后，把原始参数数组传回给 Python，避免丢字
        js="(text, history, state, file) => { const ta = document.querySelector('#input-row textarea'); if(ta) ta.value = ''; return [text, history, state, file]; }"
    )

    send_btn.click(
        fn=handle_text_with_file_generator,
        inputs=[text_input, chatbot, user_state, pending_file],
        outputs=[chatbot, text_input, pending_file, attachment_html, clear_file_btn],
        show_progress="minimal",
        # ✅ 同样修正
        js="(text, history, state, file) => { const ta = document.querySelector('#input-row textarea'); if(ta) ta.value = ''; return [text, history, state, file]; }"
    )

# ================= 启动入口（多线程分离法，彻底解决端口绑定死锁） =================
# ================= 启动入口（Gradio 4.x 最佳适配） =================
# ================= 双线程分离启动入口（Gradio 3.x 最稳方案） =================
import threading
import asyncio

def start_worker_thread():
    """在独立的线程中启动异步 Worker，绝对不干扰主线程"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 同时运行 Query 和 Command 两个 Worker 的死循环
        loop.run_until_complete(asyncio.gather(
            query_worker.run_loop(),
            command_worker.run_loop()
        ))
    except Exception as e:
        print(f"[后台 Worker 异常] {e}")

if __name__ == "__main__":
    # 初始化数据库等
    init_users_db()
    init_db()
    init_calendar()
    
    # 1. 启动后台 Worker 线程（Daemon 线程，随主线程自动退出）
    worker_thread = threading.Thread(target=start_worker_thread, daemon=True)
    worker_thread.start()
    
    # 2. 主线程立即启动 Gradio 服务（无任何 asyncio 阻塞）
    port = int(os.environ.get("PORT", 7860))
    demo.queue()
    demo.launch(
        server_name="0.0.0.0",
        server_port=port,
        theme=gr.themes.Soft(),
        css="""
            #voice-file-input { display: none; }
            #paste-file-input { display: none; }
            .loader, .spinner, .progress, .loading { display: none !important; }
            #chat-input textarea::placeholder { color: #aaa; opacity: 1; }
            
            #upload-row {
                display: flex !important; flex-direction: row !important;
                flex-wrap: nowrap !important; align-items: center !important;
                gap: 0px !important; justify-content: flex-start !important;
                margin-bottom: 10px !important;
            }
            #upload-row > div {
                flex: 0 0 auto !important; width: auto !important;
                max-width: fit-content !important; margin: 0 !important; padding: 0 !important;
            }
            #upload-row .gr-box, #upload-row .gr-box > div {
                display: flex !important; align-items: center !important;
                justify-content: center !important; background: transparent !important;
                border: none !important; box-shadow: none !important;
            }
            #upload-row button {
                white-space: nowrap !important; display: flex !important;
                flex-direction: row !important; align-items: center !important;
                gap: 4px !important; min-width: auto !important; padding: 4px 8px !important;
            }
            #attachment-html {
                flex: 0 0 auto !important; width: auto !important;
                margin: 0 6px !important; padding: 0 !important;
            }
            #clear-btn {
                flex: 0 0 auto !important; width: auto !important;
                margin: 0 0 0 4px !important; padding: 0 2px !important;
                background: transparent !important; border: none !important;
                box-shadow: none !important; color: #ff5555 !important; font-size: 14px !important;
                font-weight: bold !important; min-width: auto !important; cursor: pointer !important;
            }
        """
    )
