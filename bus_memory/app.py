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
                tenant_dropdown = gr.Dropdown(choices=get_available_tenants(), value="default", label="当前租户", interactive=False, scale=1)
                refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

            with gr.Row():
                user_display = gr.Markdown("")
                logout_btn = gr.Button("退出登录", size="sm")

            chatbot = gr.Chatbot(label="对话", height=500, value=[])

            # 上传按钮和附件提示放在同一行
            with gr.Row(elem_id="upload-row"):
                file_upload_btn = gr.UploadButton(
                    "📎 上传文件",
                    file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                    scale=0
                )
                attachment_msg = gr.Markdown("", elem_id="attachment-msg", scale=1)

            # 输入框
            text_input = gr.Textbox(
                show_label=False,
                placeholder="发送消息或按住空格说话，松开发送...",
                scale=4
            )

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
            return None, ""
        file_path = file.name if hasattr(file, 'name') else str(file)
        file_name = os.path.basename(file_path)
        return file_path, f"📎 {file_name}"

    file_upload_btn.upload(fn=handle_file_upload, inputs=[file_upload_btn], outputs=[pending_file, attachment_msg])

    # ================= 文本提交 =================
    async def handle_text_with_file(text, history, user_state, pending_file_val):
        if not user_state:
            return history or [], "", None, ""
        session_id = user_state.get("username", "default")
        memory.set_tenant(session_id, user_state.get("tenant", session_id))
        history = history or []
        file_name = None
        if pending_file_val:
            file_path = pending_file_val
            ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            # 这里可以后续扩展为真正的文件分析
            file_result = "文件内容待分析"
            memory.set_file_context(session_id, f"【上传文件：{file_name}】\n{file_result}")
            memory.add_uploaded_file(session_id, file_name, file_result)
        if file_name and text.strip():
            display_msg = f"📎 上传文件：{file_name}\n{text}"
        elif file_name and not text.strip():
            display_msg = f"📎 上传文件：{file_name}"
        else:
            display_msg = text
        history.append({"role": "user", "content": display_msg})
        if text.strip():
            answer = await chat_core(session_id, text, query_worker, command_worker, TOOL_ROUTER)
        else:
            answer = "文件已就绪，您可以基于该内容提问。"
        history.append({"role": "assistant", "content": answer})
        return history, "", None, ""

    text_input.submit(fn=handle_text_with_file, inputs=[text_input, chatbot, user_state, pending_file], outputs=[chatbot, text_input, pending_file, attachment_msg])

# ================= 启动入口 =================
if __name__ == "__main__":
    init_users_db()
    init_db()
    init_calendar()
    loop = asyncio.get_event_loop()
    loop.create_task(query_worker.run_loop())
    loop.create_task(command_worker.run_loop())
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Soft())