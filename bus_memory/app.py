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

# ================= 自动清理损坏的日志文件 =================
def check_and_repair_log_file():
    if os.path.exists("plan_log.json"):
        try:
            with open("plan_log.json", "r", encoding="utf-8") as f:
                f.readlines()
        except Exception as e:
            print(f"[日志] 日志文件损坏，自动删除重建: {e}")
            os.remove("plan_log.json")
            print("[日志] 已自动删除损坏的 plan_log.json")

check_and_repair_log_file()

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
    let spacePressTimer = null;
    let originalInputValue = "";

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

    async function startRecording() {
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
    }

    document.addEventListener('keydown', (e) => {
        if (e.code !== 'Space' || isRecording) return;

        const active = document.activeElement;
        const isInput = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');

        // 【核心修复】保存原始输入内容，不拦截默认行为（光标正常移动/拼音选词）
        if (isInput) {
            originalInputValue = active.value;
        } else {
            e.preventDefault(); // 页面防滚动
        }

        // 【核心修复】长按300ms才触发录音，完美避免拼音选词误触
        spacePressTimer = setTimeout(() => {
            spacePressTimer = null;
            startRecording();
        }, 300);
    });

    document.addEventListener('keyup', (e) => {
        if (e.code !== 'Space') return;

        // 如果还没到300ms就松开了，取消定时器，绝不当做语音输入
        if (spacePressTimer) {
            clearTimeout(spacePressTimer);
            spacePressTimer = null;
            return;
        }

        if (!isRecording) return;
        
        const active = document.activeElement;
        const isInput = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');

        // 停止录音并清理多余的空格
        if (isInput) {
            // 还原输入框内容，清理长按产生的空格
            active.value = originalInputValue;
        } else {
            e.preventDefault();
        }

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
    user_state = gr.State(value=None)
    session_user_input = gr.Textbox(visible=False)
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
            gr.Markdown("## 🚀 企业AI原生系统")
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

                            # 项目列表
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

                                    # 文件名（一直显示，但由CSS控制透明）
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
                        refresh_logs_btn = gr.Button("🔄 刷新日志", elem_id="refresh-logs-btn")
                        clear_logs_btn = gr.Button("🗑️ 清空日志", variant="secondary", elem_id="clear-logs-btn")
                    
                    logs_table = gr.Dataframe(
                        headers=["时间", "用户", "角色", "动作", "详情", "状态"],
                        interactive=False,
                        wrap=True,
                        elem_id="logs-table"
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
                            file_types=[".txt", ".md", ".csv", ".pdf", ".docx"],  # 【修复】支持 PDF 和 Word
                            type="filepath"
                        )
                        kb_tags_input = gr.Textbox(
                            label="元数据标签（可选，用逗号分隔）",
                            placeholder="例如：财务报表, 产品文档"
                        )
                        kb_index_btn = gr.Button("🚀 提交索引", variant="primary")
                    kb_status = gr.Markdown("")

        # 隐藏的用户状态组件（供后端 outputs 使用，不显示在界面上）
        user_display = gr.Markdown("", visible=False)


    # ================= 底层逻辑与事件绑定 =================
    # ================= 日志读取与清空 =================
    def load_logs(user=None):
        import os
        if not os.path.exists("plan_log.json"):
            return []
        logs_data = []
        try:
            # 【核心防御】用二进制模式读取，确保不因为损坏字符报错
            with open("plan_log.json", "rb") as f:
                raw_content = f.read()
            text_content = raw_content.decode("utf-8", errors="ignore")
            lines = text_content.splitlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    timestamp = entry.get('timestamp', '未知时间')
                    username = entry.get('username', 'unknown')
                    role = entry.get('role', 'unknown')
                    mode = entry.get('mode', '系统')
                    user_query = entry.get('user_query', '')
                    tool = entry.get('tool', '')
                    
                    if mode == 'plan':
                        action = "生成工作计划"
                        detail = user_query if user_query else "系统自动生成计划"
                    elif tool == 'file_upload':
                        action = "上传文件"
                        detail = user_query if user_query else "上传文件"
                    elif tool == 'get_current_time':
                        action = "查询当前时间"
                        detail = user_query if user_query else "询问当前时间"
                    elif tool == 'knowledge_search':
                        action = "知识库检索"
                        detail = user_query if user_query else "检索知识库"
                    elif tool == 'knowledge_index':
                        action = "知识库索引"
                        detail = user_query if user_query else "上传知识文档"
                    elif tool == 'web_search':
                        action = "联网搜索"
                        detail = user_query if user_query else "搜索内容"
                    elif tool == 'query_database':
                        action = "查询数据库"
                        detail = user_query if user_query else "查询数据"
                    else:
                        action = tool if tool else "系统操作"
                        detail = user_query if user_query else ""
                    
                    status = entry.get('status', 'success')
                    status_map = {'success': '成功', 'failed': '失败', 'error': '错误'}
                    status = status_map.get(status, status)
                    
                    logs_data.append([timestamp, username, role, action, detail, status])
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            print(f"[日志] 加载日志异常，已阻止崩溃: {e}")
            return []
        
        return logs_data if logs_data else []

    def clear_logs():
        import os
        try:
            if os.path.exists("plan_log.json"):
                os.remove("plan_log.json")
            return []
        except Exception as e:
            print(f"[日志] 清空日志异常: {e}")
            return []

    # 日志面板的刷新与清空（必须在 UI 定义后绑定）
    refresh_logs_btn.click(
        fn=load_logs,
        inputs=[],
        outputs=[logs_table],
        show_progress="hidden"
    )
    clear_logs_btn.click(
        fn=clear_logs,
        inputs=[],
        outputs=[logs_table],
        show_progress="hidden"
    )


    # ================= 登录、退出、加载函数 =================
    def login(username, pin):
        user = authenticate(username.strip().lower(), pin)
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
                f"✅ 登录成功，欢迎 {user['display_name']}！",
                f"**当前用户：{user_full}**",
                gr.update(visible=(user.get("role") == "admin")),
                f"**当前用户：** {user_full}",
                gr.update(visible=(user.get("role") == "admin"))
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
                gr.update(visible=False)
            )

    # 登录事件绑定
    login_btn.click(
        fn=login,
        inputs=[username_input, pin_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        js="(username, pin) => { sessionStorage.setItem('suo_user', username); window.history.replaceState({}, '', '/?user=' + username + '&project=主对话'); return [username, pin]; }",
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
            gr.update(visible=False)
        )

    # 退出登录事件绑定
    logout_btn.click(
        fn=logout,
        inputs=[],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        js="() => { sessionStorage.removeItem('suo_user'); window.history.replaceState({}, '', '/'); return true; }",
        show_progress="hidden"
    )

    def load_history(session_username, session_project=None):
        if session_username:
            user = get_user_info(session_username)
            if user:
                session_id = f"{user['username']}_{session_project or '主对话'}"
                memory.set_tenant(session_id, user["tenant"])
                memory.set_current_user(user)
                hist = memory.get_history(session_id)
                tenants = get_available_tenants()
                user_full = f"{user['display_name']} ({user['department']} - {user['position']})"
                
                # 【核心修复】通过新增的安全方法获取项目列表，彻底解决 memory_store 报错
                new_project_names = memory.get_all_projects(user['username'])
                if session_project not in new_project_names:
                    new_project_names.append(session_project)

                return (
                    user,
                    gr.update(visible=False),
                    gr.update(visible=True),
                    hist if hist else [],
                    gr.Dropdown(choices=tenants, value=user["tenant"]),
                    gr.update(choices=new_project_names, value=session_project or "主对话"),
                    session_project or "主对话",
                    new_project_names,
                    "",
                    f"**当前用户：{user_full}**",
                    gr.update(visible=(user.get("role") == "admin")),
                    f"**当前用户：** {user_full}",
                    gr.update(visible=(user.get("role") == "admin"))
                )
        return (
            None,
            gr.update(visible=True),
            gr.update(visible=False),
            [],
            gr.Dropdown(choices=get_available_tenants(), value="default"),
            gr.update(choices=["主对话"], value="主对话"),
            "主对话",
            ["主对话"],
            "",
            "",
            gr.update(visible=False),
            "**当前用户：** 未登录",
            gr.update(visible=False)
        )

    # 页面加载事件绑定
    demo.load(
        fn=load_history,
        inputs=[session_user_input],
        outputs=[user_state, login_column, chat_column, chatbot, tenant_dropdown, project_list, current_project, project_names, login_msg, user_display, workflow_tab, current_user_display, user_management_tab],
        js="""() => {
            const urlParams = new URLSearchParams(window.location.search);
            const user = urlParams.get('user') || sessionStorage.getItem('suo_user') || '';
            const project = urlParams.get('project') || '主对话';
            if (user) sessionStorage.setItem('suo_user', user);
            // 修复刷新后白屏卡顿：直接通过 URL 同步返回用户和项目
            return [user, project];
        }""",
        show_progress="hidden"
    )


    # ================= 知识库事件绑定 =================
    def handle_kb_index(file, kb_tags, user, current_project):
        if not user:
            return "❌ 请先登录！"
        if not file:
            return "❌ 请先上传文件！"
        
        # 【修复】安全处理 Gradio 文件对象，确保一定是字符串路径
        file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
        
        session_id = f"{user['username']}_{current_project}"
        msg = index_document(file_path, session_id, kb_tags)
        simple_log_tool(session_id, f"上传知识库文件:{file_path}", "knowledge_index", {"file_path": file_path, "tags": kb_tags}, msg)
        return msg

    kb_index_btn.click(
        fn=handle_kb_index,
        inputs=[kb_upload, kb_tags_input, user_state, current_project],
        outputs=[kb_status],
        show_progress="hidden"
    )


    # ================= 用户管理逻辑 =================
    def load_users():
        import sqlite3, os
        try:
            db_path = os.path.join(os.getcwd(), "users.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT username, display_name, department, position, role, tenant FROM users ORDER BY id ASC")
            data = cursor.fetchall()
            conn.close()
            return list(data)
        except Exception as e:
            return [["读取失败", str(e), "", "", "", ""]]

    def create_user(username, pin, display_name, department, position, role):
        import sqlite3, os
        try:
            db_path = os.path.join(os.getcwd(), "users.db")
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, display_name, pin, department, position, role, tenant) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username.strip().lower(), display_name, pin, department, position, role, username.strip().lower())
            )
            conn.commit()
            conn.close()
            return f"✅ 用户 {username} 创建成功！", gr.update(value=list(load_users().value))
        except Exception as e:
            return f"❌ 创建失败：{e}", gr.update(value=[])

    refresh_users_btn.click(fn=load_users, inputs=[], outputs=[users_table])
    create_user_btn.click(
        fn=create_user,
        inputs=[new_username, new_pin, new_display_name, new_department, new_position, new_role],
        outputs=[create_user_msg, users_table]
    )


    # ================= 主处理函数（文本、文件、音频） =================
    async def unified_handler(message, history, file, user, current_project):
        if not user:
            return history or [], "", None, "", ""

        history = list(history) if history else []
        session_id = f"{user.get('username', 'default')}_{current_project or '主对话'}"
        memory.set_tenant(session_id, user.get("tenant", session_id))

        # 文件处理
        if file is not None:
            file_path = file if isinstance(file, str) else (file.name if hasattr(file, 'name') else str(file))
            ext = os.path.splitext(file_path)[1].lower()
            file_name = os.path.basename(file_path)
            file_result = ""
            
            # ========== 语音文件特殊处理（防止上下文爆炸和时间卡死） ==========
            if ext in ('.wav', '.mp3', '.m4a', '.ogg', '.webm'):
                file_result = await asyncio.to_thread(speech_to_text, file_path)
                # 强制截断防止异常超长字符串进入上下文
                if len(file_result) > 2000:
                    file_result = file_result[:2000] + "...(内容过长，已截断)"
                # 存入记忆，但仅作简要记录
                memory.set_file_context(session_id, f"【用户语音转写】{file_result}")
                memory.add_uploaded_file(session_id, file_name, file_result)
                
                # 【核心修复】如果用户没有输入文字，直接把语音转写的结果作为用户的问题！
                if not message or not message.strip():
                    message = file_result
                elif message and message.strip():
                    message = f"{message}\n(用户语音补充：{file_result})"

                # 【精确修复】区分“按住空格录音”和“上传语音文件”的显示格式
                if ext == '.webm':
                    # 按空格录制的语音：不显示文件名，只显示上传语音和转写的文字
                    history.append({"role": "user", "content": f"📎 上传语音：{message}"})
                else:
                    # 上传的语音文件：显示文件名和转写的文字
                    history.append({"role": "user", "content": f"📎 上传文件：{file_name}，{message}"})
            
            # ========== 其他文件处理（图片/CSV/Excel） ==========
            else:
                if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
                    file_result = await asyncio.to_thread(ocr_image, file_path)
                elif ext in ('.csv', '.xlsx', '.xls'):
                    file_result = await asyncio.to_thread(analyze_file, file_path)
                else:
                    file_result = "不支持的文件类型"

                file_result = str(file_result)
                memory.set_file_context(session_id, f"【上传文件：{file_name}】\n{file_result}")
                memory.add_uploaded_file(session_id, file_name, file_result)
                simple_log_tool(session_id, file_name, "file_upload", {"file_name": file_name}, "文件上传成功")

                history.append({"role": "user", "content": f"📎 上传文件：{file_name}"})
                if message and message.strip():
                    history.append({"role": "user", "content": message})

            answer = await chat_core(session_id, message if message else f"请帮我分析文件 {file_name} 的内容", query_worker, command_worker, TOOL_ROUTER)
            history.append({"role": "assistant", "content": answer})
            return history, "", None, message, answer

        # 纯文本处理
        if not message or not message.strip():
            return history, "", None, "", ""

        history.append({"role": "user", "content": message})
        answer = await chat_core(session_id, message, query_worker, command_worker, TOOL_ROUTER)
        history.append({"role": "assistant", "content": answer})
        return history, "", None, message, answer


    # 纯文本及混合输入事件
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


    # ================= 文件暂存与清除事件 =================
    def handle_file_upload(file):
        if file is None:
            return None, "", gr.update(visible=False)
        file_path = file.name if hasattr(file, 'name') else str(file)
        file_name = os.path.basename(file_path)
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

            /* === 隐藏所有 Gradio 加载动画和飞镖（包括发送时的遮罩层） === */
            .gradio-container .loading,
            .gradio-container .progress-bar,
            .gradio-container .progress-text,
            .gradio-container .status-tracker,
            .gradio-container .spinner,
            .gradio-container .loading-container,
            .gradio-container .loading-icon,
            button .loading,
            button .spin,
            .gradio-container .progress-container {
                display: none !important;
                opacity: 0 !important;
                visibility: hidden !important;
                width: 0 !important;
                height: 0 !important;
                pointer-events: none !important;
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
                gap: 0 !important;
                margin-bottom: 5px !important;
                width: 100% !important;
            }
            #file-row-spacer {
                flex-grow: 1 !important;
                min-width: 0 !important;
            }

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