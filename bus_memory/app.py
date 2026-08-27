# bus_memory/app.py

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import gradio as gr
import asyncio
import json
from datetime import datetime, timedelta
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
from common.rag import index_document


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


# ================= 自定义 JavaScript（按住空格录音） =================
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

    function showStatus(text) {
        statusDiv.textContent = text;
        statusDiv.style.display = 'block';
    }
    function hideStatus() {
        statusDiv.style.display = 'none';
    }

    document.addEventListener('keydown', async (e) => {
        if (e.code !== 'Space' || isRecording) return;

        const active = document.activeElement;
        if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA') && active.value && active.value.trim() !== '') {
            return;
        }

        e.preventDefault();
        isRecording = true;
        audioChunks = [];
        showStatus('🎤 正在录音...');

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            mediaRecorder = new MediaRecorder(stream);
            mediaRecorder.ondataavailable = event => {
                if (event.data.size > 0) audioChunks.push(event.data);
            };
            mediaRecorder.onstop = () => {
                hideStatus();
                const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
                const file = new File([audioBlob], 'voice_message.webm', { type: audioBlob.type });

                const wrapper = document.getElementById('voice-file-input');
                const fileInput = wrapper ? wrapper.querySelector('input[type="file"]') : null;
                if (fileInput) {
                    const dt = new DataTransfer();
                    dt.items.add(file);
                    fileInput.files = dt.files;
                    fileInput.dispatchEvent(new Event('change', { bubbles: true }));
                }
                mediaRecorder.stream.getTracks().forEach(track => track.stop());
                mediaRecorder = null;
            };
            mediaRecorder.start();
        } catch (err) {
            console.error('录音失败:', err);
            showStatus('❌ 无法访问麦克风，请检查权限');
            setTimeout(hideStatus, 2000);
            isRecording = false;
        }
    });

    document.addEventListener('keyup', (e) => {
        if (e.code !== 'Space' || !isRecording) return;
        e.preventDefault();
        isRecording = false;
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            mediaRecorder.stop();
        }
    });
})();
</script>
"""


with gr.Blocks(title="AI 智能体") as demo:
    # ---------- 全局状态 ----------
    user_state = gr.State(value=None)              # 当前登录用户信息
    session_user_input = gr.Textbox(visible=False)   # 用于接收 sessionStorage 中的用户名
    last_user_message = gr.State("")
    last_assistant_message = gr.State("")
    feedback_up = gr.State("up")
    feedback_down = gr.State("down")
    pending_file = gr.State(None)
    current_project = gr.State("主对话")
    project_names = gr.State(["主对话"])

    # ---------- 登录界面 ----------
    with gr.Row(elem_id="login-wrapper"):
        with gr.Column(scale=1, elem_id="login-box") as login_column:
            gr.HTML("""
                <div style="display: flex; align-items: center; justify-content: center; gap: 20px; margin-bottom: 40px;">
                    <img src="data:image/png;base64,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" style="height: 70px; width: auto; object-fit: contain;" alt="企业Logo">
                    <h1 style="margin: 0; font-size: 34px; font-weight: 800; color: #1E4D8C;">企业AI原生系统</h1>
                </div>
            """)
        
            username_input = gr.Textbox(label="用户名：")
            pin_input = gr.Textbox(label="密码：", type="password")
        
            login_btn = gr.Button("登录", variant="primary")
            login_msg = gr.Markdown("")

    # ---------- 主聊天界面 ----------
    with gr.Column(visible=False) as chat_column:
        # ================= 顶部品牌栏 =================
        with gr.Row(elem_id="top-brand-bar"):
            gr.HTML("""
                <div style="display:flex; align-items:center; gap:10px;">
                    <h2 style="margin:0; color:#1E4D8C;">🚀 某某企业AI原生系统平台</h2>
                    <span style="font-size:14px; color:#888;">(AI智能体系统+记忆+知识库+工具)</span>
                </div>
            """)
            logout_btn = gr.Button("退出登录", elem_id="top-logout-btn", scale=0, min_width=0)

        # ================= 顶部导航 + 退出登录 =================
        with gr.Row(elem_id="top-nav-container"):
            with gr.Tabs(elem_id="top-nav-bar") as main_tabs:

                # ================= 聊天 Tab =================
                with gr.Tab("聊天"):
                    with gr.Row(elem_id="core-work-area"):

                        # 左侧 1/3：项目侧边栏
                        with gr.Column(scale=1, min_width=280, elem_id="project-sidebar"):
                            # 隐藏的租户下拉框（仅参与逻辑）
                            tenant_dropdown = gr.Dropdown(
                                choices=get_available_tenants(),
                                value="default",
                                label="",
                                interactive=False,
                                visible=False
                            )

                            # 当前用户展示
                            with gr.Row(elem_id="current-user-row"):
                                current_user_display = gr.Markdown(
                                    value="**当前用户：** 未登录",
                                    elem_id="current-user-display"
                                )

                            # 项目操作：默认显示“项目+”和“删除当前项目”
                            with gr.Row(elem_id="project-actions-row"):
                                add_project_btn = gr.Button("项目 +", elem_id="add-project-btn", scale=0, visible=True)
                                delete_project_btn = gr.Button("🗑️ 删除当前项目", visible=True, scale=0, elem_id="delete-project-btn")
                            
                            with gr.Row(elem_id="project-creation-row", visible=False) as project_creation_row:
                                project_input = gr.Textbox(placeholder="输入项目名称...", scale=3, show_label=False, elem_id="project-input-box")
                                create_project_btn = gr.Button("创建", scale=1, min_width=60, elem_id="create-project-btn")
                                cancel_project_btn = gr.Button("×", scale=1, min_width=40, elem_id="cancel-project-btn")

                            # 项目列表（白色小卡片样式）
                            project_list = gr.Radio(
                                choices=["主对话"],
                                value="主对话",
                                label="项目列表",
                                interactive=True,
                                visible=True,
                                elem_id="project-list"
                            )

                        # 右侧 3/4：聊天主区
                        with gr.Column(scale=3, elem_id="chat-main-area"):
                            # 聊天框
                            chatbot = gr.Chatbot(label="对话", height=500, value=[], show_label=False)
                            
                            # ========== 底部合并卡片式输入区 ==========
                            with gr.Group(elem_id="input-card"):
                                # 第一行：反馈 + 占位符 + 文件名/❌（靠右）
                                with gr.Row(elem_id="file-row"):
                                    up_btn = gr.Button("👍 有帮助", scale=0, min_width=90, size="sm")
                                    down_btn = gr.Button("👎 无帮助", scale=0, min_width=90, size="sm")
                                    feedback_msg = gr.Markdown("")

                                    # 占位符：把后续元素推到最右
                                    spacer = gr.Markdown("", scale=4, elem_id="file-row-spacer")

                                    # 文件名（【关键】一直显示，但由CSS控制透明，有内容时才显现文字）
                                    attachment_html = gr.Markdown(
                                        value="",
                                        visible=True,
                                        elem_id="attachment-html"
                                    )

                                    # ❌ 清除按钮（默认隐藏）
                                    clear_file_btn = gr.Button("×", scale=0, min_width=40, elem_id="clear-btn", visible=False)

                                # 第二行：输入框 + 占位符 + 📎 + 发送按钮
                                with gr.Row(elem_id="input-row-final"):
                                    text_input = gr.Textbox(
                                        show_label=False,
                                        placeholder="发消息或按住空格说话，松开发送...",
                                        scale=1
                                    )
                                    # 占位符：大幅缩短输入框，增加右侧留白
                                    input_spacer = gr.Markdown("", scale=3, elem_id="input-row-spacer")
                                    
                                    file_upload_btn = gr.UploadButton(
                                        "📎",
                                        file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                                        scale=0, min_width=60, elem_id="upload-icon-btn"
                                    )
                                    send_btn = gr.Button("⬆", scale=0, min_width=60, elem_id="send-btn")

                            # 隐藏的语音输入组件
                            voice_file_input = gr.File(
                                visible=True,
                                type="filepath",
                                elem_id="voice-file-input",
                                label=""
                            )

                # ================= 系统健康 Tab =================
                with gr.Tab("系统健康"):
                    gr.Markdown("## 🏥 系统健康仪表板")
                    health_refresh_btn = gr.Button("刷新数据")
                    health_summary_md = gr.Markdown("加载中...")
                    health_tool_table = gr.Dataframe(
                        headers=["工具名称", "调用次数"],
                        interactive=False
                    )

                # ================= 状态监控 Tab =================
                with gr.Tab("状态监控"):
                    gr.Markdown("## 实时 Worker 状态")
                    refresh_btn2 = gr.Button("刷新")
                    status_table = gr.Dataframe(
                        headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度", "平均耗时(s)", "错误率"],
                        interactive=False
                    )

                # ================= 工作流管理 Tab =================
                with gr.Tab("工作流管理", visible=False) as workflow_tab:
                    gr.Markdown("## 🧩 低代码工作流配置")
                    workflow_name_input = gr.Textbox(label="工作流名称")
                    workflow_desc_input = gr.Textbox(label="描述")
                    workflow_steps_input = gr.Textbox(
                        label="步骤 JSON",
                        placeholder='[{"tool": "get_current_time", "arguments": {}}, {"tool": "web_search", "arguments": {"query": "今日新闻"}}]'
                    )
                    workflow_create_btn = gr.Button("创建工作流")
                    workflow_create_msg = gr.Markdown("")
                    refresh_workflow_btn = gr.Button("刷新列表")
                    workflow_list = gr.Dataframe(
                        headers=["名称", "描述", "创建者", "创建时间"],
                        interactive=False
                    )

                # ================= 日志 Tab =================
                with gr.Tab("日志"):
                    gr.Markdown("## 📜 系统运行日志")
                    with gr.Row():
                        refresh_logs_btn = gr.Button("🔄 刷新日志")
                        clear_logs_btn = gr.Button("🗑️ 清空日志", variant="secondary")
                    
                    # 用 Dataframe 替代 Textbox，让日志结构清晰可见
                    logs_table = gr.Dataframe(
                        headers=["时间", "用户", "角色", "动作", "详情", "状态"],
                        interactive=False,
                        wrap=True
                    )

                # ================= 用户管理 Tab（仅管理员可见） =================
                with gr.Tab("用户管理", visible=False) as user_management_tab:
                    gr.Markdown("## 👥 系统用户管理")
                    
                    with gr.Row():
                        refresh_users_btn = gr.Button("🔄 刷新用户列表")
                    
                    users_table = gr.Dataframe(
                        headers=["用户名", "姓名", "部门", "职位", "角色", "租户"],
                        interactive=False
                    )
                    
                    # 将之前的 markdown 改为普通说明（不再需要点击）
                    gr.Markdown("### 直接在下方填写信息，点击“创建用户”即可新增")
                    
                    with gr.Row():
                        new_username = gr.Textbox(label="用户名 (小写)", scale=1)
                        new_pin = gr.Textbox(label="密码", type="password", scale=1)
                        new_display_name = gr.Textbox(label="姓名", scale=1)
                        new_department = gr.Textbox(label="部门", scale=1)
                        new_position = gr.Textbox(label="职位", scale=1)
                        new_role = gr.Dropdown(choices=["developer", "manager", "admin", "viewer"], value="viewer", label="角色", scale=1)
                    
                    with gr.Row():
                        create_user_btn = gr.Button("创建用户", variant="primary")
                    create_user_msg = gr.Markdown("")
                    
                # ================= 知识库 Tab =================
                with gr.Tab("知识库"):
                    gr.Markdown("## 📚 企业垂直知识库（RAG）")
                    gr.Markdown("上传文档（txt/md/csv），可添加标签以便检索时精准过滤。")
                    with gr.Row():
                        kb_upload = gr.File(
                            label="上传知识文档",
                            file_types=[".txt", ".md", ".csv"],
                            type="filepath"
                        )
                        # ✅ 新增：标签输入框
                        kb_tags_input = gr.Textbox(
                            label="元数据标签（可选，用逗号分隔）",
                            placeholder="例如：财务报表, 产品文档"
                        )
                        kb_index_btn = gr.Button("🚀 提交索引", variant="primary")
                    
                    kb_status = gr.Markdown("")


        # 隐藏的用户状态组件（供后端 outputs 使用，不显示在界面上）
        user_display = gr.Markdown("", visible=False)
        

    # ================= 日志读取与清空逻辑 =================
    # 日志读取（支持用户隔离）
    def load_logs(user):
        import os
        if not user:
            return []
        
        # 获取当前登录用户名
        current_username = user.get("username", "")
        
        if not os.path.exists("plan_log.json"):
            return []
        
        logs_data = []
        with open("plan_log.json", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    
                    # 提取原始数据
                    timestamp = entry.get('timestamp', '未知时间')
                    session_id = entry.get('session_id', '')
                    username = entry.get('username', 'unknown')
                    
                    # 从 session_id（如 alice_产品部）中提取真实用户名
                    real_username = session_id.split('_')[0] if '_' in session_id else session_id
                    if username == 'unknown' and real_username:
                        username = real_username
                        
                    # ✅ 核心修复：如果这个日志不是当前用户的，直接跳过
                    if username != current_username:
                        continue
                    
                    # 时间转换逻辑
                    try:
                        from datetime import datetime, timedelta
                        dt = datetime.fromisoformat(timestamp)
                        timestamp = (dt + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        pass
                    
                    role = '企业用户' if username != 'admin' else '系统管理员'
                    mode = entry.get('mode', 'regular')
                    tool = entry.get('tool', '')
                    user_query = entry.get('user_query', '')
                    
                    # 根据业务语义转换“动作”和“详情”
                    if mode == 'plan':
                        action = "生成工作计划"
                        detail = user_query if user_query else "系统自动生成计划"
                    elif tool == 'file_upload':
                        action = "上传文件"
                        detail = entry.get('file_name', '') if entry.get('file_name') else (user_query if user_query else "上传文件")
                    elif tool == 'get_current_time':
                        action = "查询当前时间"
                        detail = user_query if user_query else "询问当前时间"
                    elif tool == 'web_search':
                        action = "联网搜索"
                        detail = user_query if user_query else "搜索内容"
                    elif tool == 'query_database':
                        action = "查询数据库"
                        detail = user_query if user_query else "查询数据"
                    elif tool == 'speech_to_text':
                        action = "语音输入"
                        detail = user_query if user_query else "语音转文字"
                    else:
                        action = tool if tool else "系统操作"
                        detail = user_query if user_query else ""
                    
                    status = entry.get('status', entry.get('final_status', 'success'))
                    status_map = {'success': '成功', 'failed': '失败', 'error': '错误'}
                    status = status_map.get(status, status)
                    
                    logs_data.append([timestamp, username, role, action, detail, status])
                except json.JSONDecodeError:
                    continue
                    
        return logs_data

    def clear_logs():
        if os.path.exists("plan_log.json"):
            os.remove("plan_log.json")
        return []


    # ================= 登录、退出、加载函数 =================
    def login(username, pin):
        user = authenticate(username.strip().lower(), pin)
        if user:
            session_id = user["username"]
            memory.set_tenant(session_id, user["tenant"])
            memory.set_current_user(user)
            hist = memory.get_history(session_id)
            tenants = get_available_tenants()
            
            # 1. user_full 只保留用户名字和部门，不包含前缀
            user_full = f"{user['display_name']} ({user['department']} - {user['position']})"
            
            return (
                user,
                gr.update(visible=False),
                gr.update(visible=True),
                hist if hist else [],
                gr.Dropdown(choices=tenants, value=user["tenant"]),
                f"✅ 登录成功，欢迎 {user['display_name']}！",
                f"**当前用户：{user_full}**",
                gr.update(visible=(user.get("role") == "admin")),
                f"**当前用户：** {user_full}",
                gr.update(visible=(user.get("role") == "admin"))  # ✅ 新增：控制用户管理Tab
            )
        else:
            return (
                None,
                gr.update(visible=True),
                gr.update(visible=False),
                [],
                gr.Dropdown(choices=get_available_tenants(), value="default"),
                "❌ 用户名或 PIN 码错误",
                "",
                gr.update(visible=False),
                "**当前用户：** 未登录",
                gr.update(visible=False)  # ✅ 新增：控制用户管理Tab
            )

    # 1. 登录事件绑定（同步 URL 和 sessionStorage）
    login_btn.click(
        fn=login,
        inputs=[username_input, pin_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        # ✅ 核心：登录成功后，将用户名存入 sessionStorage，并动态修改 URL
        js="(username, pin) => { sessionStorage.setItem('suo_user', username); window.history.replaceState({}, '', '/?user=' + username); return [username, pin]; }",
        show_progress="hidden"
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
            gr.update(visible=False),
            "**当前用户：** 未登录",
            gr.update(visible=False)  # ✅ 新增：控制用户管理Tab
        )

    # 2. 退出登录事件绑定（清除状态）
    logout_btn.click(
        fn=logout,
        inputs=[],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        # ✅ 核心：退出时清空 sessionStorage，并将地址栏恢复为根路径
        js="() => { sessionStorage.removeItem('suo_user'); window.history.replaceState({}, '', '/'); return true; }",
        show_progress="hidden"
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
                user_full = f"{user['display_name']} ({user['department']} - {user['position']})"
            return (
                user,
                gr.update(visible=False),
                gr.update(visible=True),
                hist if hist else [],
                gr.Dropdown(choices=tenants, value=user["tenant"]),
                "",
                f"**当前用户：{user_full}**",
                gr.update(visible=(user.get("role") == "admin")),
                f"**当前用户：** {user_full}",
                gr.update(visible=(user.get("role") == "admin"))  # ✅ 新增
            )
        return (
            None,
            gr.update(visible=True),
            gr.update(visible=False),
            [],
            gr.Dropdown(choices=get_available_tenants(), value="default"),
            "",
            "",
            gr.update(visible=False),
            "**当前用户：** 未登录",
            gr.update(visible=False)  # ✅ 新增
        )

    # 3. 页面加载事件绑定（首次进入页面读取 URL 参数）
    demo.load(
        fn=load_history,
        inputs=[session_user_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        # ✅ 核心：优先读取 URL 参数，其次读取 sessionStorage
        js="() => { const urlParams = new URLSearchParams(window.location.search); const user = urlParams.get('user') || sessionStorage.getItem('suo_user') || ''; if (user) sessionStorage.setItem('suo_user', user); return user; }",
        show_progress="hidden"
    )
    
    
    # ================= 知识库事件绑定 =================
    from common.rag import index_document

    def handle_kb_index(file, kb_tags, user, current_project):
        if not user:
            return "❌ 请先登录！"
        if not file:
            return "❌ 请先上传文件！"
        
        # 获取真实的会话ID（用户 + 项目），确保不同项目知识库隔离
        session_id = f"{user['username']}_{current_project}"
        
        # 调用纯文本索引函数
        msg = index_document(file, session_id, kb_tags)
        return msg

    # 注意：绑定的时候传入4个输入！
    kb_index_btn.click(
        fn=handle_kb_index,
        inputs=[kb_upload, kb_tags_input, user_state, current_project],
        outputs=[kb_status],
        show_progress="hidden"  # 隐藏不必要的加载动画
    )


    # ================= 主处理函数（文本、文件、音频） =================
    async def unified_handler(message, history, file, user, current_project):
        if not user:
            return history or [], "", None, "", ""

        # 确保历史记录是列表类型
        history = list(history) if history else []

        # 生成包含项目信息的会话 ID
        session_id = f"{user.get('username', 'default')}_{current_project or '主对话'}"
        memory.set_tenant(session_id, user.get("tenant", session_id))

        # ================= 文件处理 =================
        if file is not None:
            file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
            ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_result = ""

            # 1. 图片处理
            if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
                if message and "表格" in message:
                    file_result = await asyncio.to_thread(recognize_table, file_path)
                else:
                    file_result = await asyncio.to_thread(ocr_image, file_path)
            # 2. 表格处理
            elif ext in ('.csv', '.xlsx', '.xls'):
                file_result = await asyncio.to_thread(analyze_file, file_path)
            # 3. 音频处理
            elif ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
                file_result = await asyncio.to_thread(speech_to_text, file_path)
            else:
                file_result = "不支持的文件类型"

            file_result = str(file_result)
            memory.set_file_context(session_id, f"【上传文件：{file_name}】\n{file_result}")
            memory.add_uploaded_file(session_id, file_name, file_result)
            simple_log_tool(session_id, file_name, "file_upload", {"file_name": file_name}, "文件上传成功")

            # 处理音频文件（直接走语音识别）
            if ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
                history.append({"role": "user", "content": f"🎤 语音输入：{file_result}"})
                answer = await chat_core(session_id, file_result, query_worker, command_worker, TOOL_ROUTER)
                history.append({"role": "assistant", "content": answer})
                return history, "", None, file_result, answer
            else:
                # 处理普通文件（CSV/Excel/图片）
                history.append({"role": "user", "content": f"📎 上传文件：{file_name}"})
                
                # 如果同时输入了文字，就把文字加进去
                if message and message.strip():
                    history.append({"role": "user", "content": message})
                
                # 明确告诉系统处理文件并回答
                answer = await chat_core(session_id, message if message else f"请帮我分析文件 {file_name} 的内容", query_worker, command_worker, TOOL_ROUTER)
                history.append({"role": "assistant", "content": answer})
                return history, "", None, message, answer

        # ================= 纯文本处理（含 RAG 极速计算） =================
        if not message or not message.strip():
            return history, "", None, "", ""

        history.append({"role": "user", "content": message})
        answer = await chat_core(session_id, message, query_worker, command_worker, TOOL_ROUTER)
        history.append({"role": "assistant", "content": answer})
        return history, "", None, message, answer
        
    # ================= 纯文本及混合输入事件（极简无占位符版） =================
    async def submit_text_with_file(message, history, user_state, pending_file_val, current_project):
        if not message and not pending_file_val:
            return history, "", None, "", gr.update(visible=False), "", ""

        # 直接调用核心处理逻辑，不添加任何额外气泡
        new_history, clear_text, _, user_msg, assistant_msg = await unified_handler(
            message, history, pending_file_val, user_state, current_project
        )

        # 返回最终结果，输入框瞬间清空
        return new_history, clear_text, None, "", gr.update(visible=False), user_msg, assistant_msg


    # ================= 事件绑定（文本、文件、语音） =================
    # ================= 文件暂存与清除事件 =================
    def handle_file_upload(file):
        if file is None:
            return None, "", gr.update(visible=False)
        file_path = file.name if hasattr(file, 'name') else str(file)
        file_name = os.path.basename(file_path)
        # 直接给 Markdown 传纯文本文件名
        return file_path, f"📎 {file_name}", gr.update(visible=True)

    file_upload_btn.upload(
        fn=handle_file_upload,
        inputs=[file_upload_btn],
        outputs=[pending_file, attachment_html, clear_file_btn],
        show_progress="hidden"
    )

    def clear_file():
        return None, "", gr.update(visible=False)

    clear_file_btn.click(
        fn=clear_file,
        inputs=[],
        outputs=[pending_file, attachment_html, clear_file_btn],
        show_progress="hidden"
    )

    # ================= 纯文本及混合输入事件 =================
    async def submit_text_with_file(message, history, user_state, pending_file_val, current_project):
        if not message and not pending_file_val:
            return history, "", None, "", gr.update(visible=False), "", ""
        new_history, clear_text, _, user_msg, assistant_msg = await unified_handler(
            message, history, pending_file_val, user_state, current_project
        )
        return new_history, clear_text, None, "", gr.update(visible=False), user_msg, assistant_msg

    # 回车事件
    text_input.submit(
        fn=submit_text_with_file,
        inputs=[text_input, chatbot, user_state, pending_file, current_project],
        outputs=[chatbot, text_input, pending_file, attachment_html, clear_file_btn, last_user_message, last_assistant_message],
        show_progress="hidden"
    )

    # 点击发送按钮
    send_btn.click(
        fn=submit_text_with_file,
        inputs=[text_input, chatbot, user_state, pending_file, current_project],
        outputs=[chatbot, text_input, pending_file, attachment_html, clear_file_btn, last_user_message, last_assistant_message],
        show_progress="hidden"
    )

    # ================= 项目创建与切换事件绑定 =================
    add_project_btn.click(
        fn=lambda: (gr.update(visible=False), gr.update(visible=True)),
        inputs=None,
        outputs=[add_project_btn, project_creation_row],
        show_progress="hidden"
    )

    cancel_project_btn.click(
        fn=lambda: (gr.update(visible=True), gr.update(visible=False)),
        inputs=None,
        outputs=[add_project_btn, project_creation_row],
        show_progress="hidden"
    )

    def create_project(project_name, project_names, current_project):
        if not project_name or not project_name.strip():
            return gr.update(), "", gr.update(visible=True), gr.update(visible=False), current_project, project_names
        
        new_name = project_name.strip()
        new_choices = ["主对话"]
        for p in project_names:
            if p != "主对话":
                new_choices.append(p)
        if new_name not in new_choices:
            new_choices.append(new_name)
        
        new_project_names = new_choices.copy()
        return (
            gr.update(choices=new_choices, value=new_name),
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            new_name,
            new_project_names
        )

    create_project_btn.click(
        fn=create_project,
        inputs=[project_input, project_names, current_project],
        outputs=[project_list, project_input, add_project_btn, project_creation_row, current_project, project_names],
        show_progress="hidden"
    )

    def switch_project(new_project, user):
        if not user:
            return [], "", new_project
        if not new_project:
            new_project = "主对话"
        session_id = f"{user['username']}_{new_project}"
        new_history = memory.get_history(session_id)
        return new_history, "", new_project

    project_list.change(
        fn=switch_project,
        inputs=[project_list, user_state],
        outputs=[chatbot, text_input, current_project],
        show_progress="hidden"
    )

    # 删除项目逻辑（删除按钮现在默认可见，无需额外的切换隐藏逻辑）
    def delete_project(current_project, project_names):
        if not current_project or current_project not in project_names:
            return gr.update(), "", project_names
        
        new_names = [p for p in project_names if p != current_project]
        
        if new_names:
            new_current = new_names[0]
            return (
                gr.update(choices=new_names, value=new_current),
                new_current,
                new_names
            )
        else:
            return (
                gr.update(choices=["主对话"], value="主对话"),
                "主对话",
                ["主对话"]
            )
    
    delete_project_btn.click(
        fn=delete_project,
        inputs=[current_project, project_names],
        outputs=[project_list, current_project, project_names],
        show_progress="hidden"
    )

    # ================= 语音文件事件 =================
    async def voice_upload_handler(message, history, file, user, current_project):
        if not user:
            return history or [], "", None, "", ""
        new_history, _, _, user_msg, assistant_msg = await unified_handler(message, history, file, user, current_project)
        return new_history, "", None, user_msg, assistant_msg

    voice_file_input.upload(
        fn=voice_upload_handler,
        inputs=[text_input, chatbot, voice_file_input, user_state, current_project],
        outputs=[chatbot, text_input, voice_file_input, last_user_message, last_assistant_message]
    )

    # ================= 反馈处理 =================
    async def handle_feedback(feedback, user_msg_state, assistant_msg_state, user_state):
        if not user_state:
            return "⚠️ 请先登录。"
        if not user_msg_state or not assistant_msg_state:
            return "⚠️ 暂无可以评价的对话。"
        try:
            from common.feedback import save_feedback
            save_feedback(user_state["username"], user_msg_state, assistant_msg_state, feedback)
            return f"感谢您的反馈！({feedback})"
        except Exception as e:
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

    # ================= 健康仪表板更新 =================
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
        head=voice_script,
        css="""
            /* 隐藏语音输入组件 */
            #voice-file-input { display: none !important; }

            body, button, input, textarea, select {
                font-family: "PingFang SC", "Microsoft YaHei", "Helvetica Neue", Arial, sans-serif !important;
            }

            body, .gradio-container {
                background: linear-gradient(135deg, #f3f4f6 0%, #e0e7ff 50%, #ffffff 100%) !important;
                margin: 0 !important;
                padding-top: 0 !important;
                height: auto !important;
                display: block !important;
            }

            #top-brand-bar {
                display: flex !important;
                align-items: center !important;
                justify-content: space-between !important;
                background: white !important;
                padding: 10px 20px !important;
                border-bottom: 1px solid #e5e7eb !important;
            }

            #top-logout-btn {
                display: flex !important;
                flex-direction: row !important;
                align-items: center !important;
                white-space: nowrap !important;
                width: max-content !important;
                min-width: 80px !important;
                padding: 6px 16px !important;
                background-color: #1E4D8C !important;
                color: white !important;
                border-radius: 6px !important;
                font-weight: bold !important;
                font-size: 15px !important;
                margin-left: auto !important;
            }

            .gradio-container .loading,
            .gradio-container .progress-text,
            .gradio-container .status-tracker {
                display: none !important;
            }

            #current-user-row {
                display: flex !important;
                align-items: center !important;
                gap: 8px !important;
                margin-bottom: 15px !important;
            }
            #current-user-row p {
                white-space: nowrap !important;
                flex-shrink: 0 !important;
            }

            /* ========== 左侧侧边栏 ========== */
            #project-sidebar,
            #project-sidebar .block,
            #project-sidebar .wrap,
            #project-sidebar .gr-box,
            #project-sidebar .form {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                padding: 0 !important;
            }

            #project-actions-row {
                display: flex !important;
                flex-direction: row !important;
                align-items: center !important;
                gap: 8px !important;
                margin-bottom: 8px !important;
            }

            #project-list,
            #project-list .block,
            #project-list .wrap,
            #project-list .gr-box,
            #project-list .form {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                padding: 0 !important;
            }
            #project-list label {
                background: #ffffff !important;
                border: 1px solid #e5e7eb !important;
                border-radius: 8px !important;
                padding: 10px !important;
                margin-bottom: 8px !important;
                color: #333 !important;
            }
            #project-list label.selected {
                background: #2563EB !important;
                border: 1px solid #2563EB !important;
                color: white !important;
            }

            /* ========== 底部输入卡片 ========== */
            #input-card {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                padding: 0 !important;
                margin: 0 !important;
            }

            #file-row {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: center !important;
                gap: 0 !important;             /* 强制清除组件间隙 */
                margin-bottom: 5px !important;
                width: 100% !important;
            }
            #file-row-spacer {
                flex-grow: 1 !important;
                min-width: 0 !important;
            }

            /* 【核心修改】将文件名强制推向右侧，消除空隙 */
            #attachment-html,
            #attachment-html .block,
            #attachment-html .wrap,
            #attachment-html .gr-box,
            #attachment-html .form {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                margin: 0 !important;
                padding: 0 !important;
                font-size: 14px !important;
                color: #333 !important;
                font-weight: 500 !important;
                display: flex !important;
                justify-content: flex-end !important;
            }
            #attachment-html p {
                margin: 0 !important;
                padding: 0 !important;
            }

            /* ❌ 按钮紧贴文件名，同样清除一切多余边距 */
            #clear-btn {
                margin: 0 !important;
                padding: 0 !important;
                color: #e53e3e !important;
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                font-size: 16px !important;
                font-weight: bold !important;
                border-radius: 6px !important;
            }

            /* ========== 输入区（缩短 + 变高 + 圆角） ========== */
            #input-row-final {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: center !important;
                gap: 8px !important;
                background: transparent !important;
                padding-left: 0 !important;
                padding-right: 12px !important;
            }
            #input-row-spacer {
                flex-grow: 1 !important;
                min-width: 0 !important;
            }
            #input-row-final .block {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                margin-left: 0 !important;
                padding-left: 0 !important;
            }
            #input-row-final input {
                background: #f9fafb !important;
                border-radius: 12px !important;
                border: 1px solid #f3f4f6 !important;
                padding: 15px 12px !important;
                font-size: 16px !important;
            }

            #upload-icon-btn {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                font-size: 24px !important;
                padding: 0 !important;
                min-width: 60px !important;
                width: 60px !important;
                height: 40px !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                cursor: pointer !important;
            }

            #send-btn {
                width: 40px !important;
                height: 40px !important;
                min-width: 40px !important;
                border-radius: 50% !important;
                background-color: #2563EB !important;
                border: none !important;
                color: white !important;
                font-size: 20px !important;
                padding: 0 !important;
                flex-shrink: 0 !important;
                box-shadow: 0 2px 6px rgba(0,0,0,0.2) !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
            }

            /* 项目创建区 */
            #project-creation-row {
                display: flex !important;
                flex-direction: row !important;
                flex-wrap: nowrap !important;
                align-items: center !important;
                gap: 4px !important;
                margin-bottom: 8px !important;
            }
            #project-creation-row input {
                background: #ffffff !important;
                border: 1px solid #cbd5e1 !important;
                border-radius: 6px !important;
                padding: 8px 10px !important;
                width: 100% !important;
            }
            .loading-container, .spinner {
                display: none !important;
                opacity: 0 !important;
            }
            #cancel-project-btn {
                width: 40px !important;
                height: 40px !important;
                min-width: 40px !important;
                flex-shrink: 0 !important;
                display: flex !important;
                align-items: center !important;
                justify-content: center !important;
                border-radius: 6px !important;
                font-size: 18px !important;
                font-weight: bold !important;
            }
            #create-project-btn {
                min-width: 60px !important;
                flex-shrink: 0 !important;
            }
        """
    )