# app.py
import gradio as gr
import os
import asyncio
import json
import pandas as pd
from main import chat_core, bus, query_worker, command_worker
from memory import memory
import tools
from tools import speech_to_text, ocr_image, recognize_table, analyze_file

SESSION_ID = "render_user"

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

# 获取可用租户列表（从记忆中的tenant_map）
def get_available_tenants():
    tenants = set(memory.tenant_map.values())
    tenants.add("default")
    return sorted(list(tenants))

# 统一的消息处理函数
async def unified_handler(message, history, file, tenant_dropdown):
    """
    处理文本和文件输入。
    message: 文本输入
    history: 聊天历史
    file: 上传的文件路径（单个文件）或 None
    tenant_dropdown: 当前租户下拉框的值（暂未在此使用，但保留接口）
    """
    # 处理特殊命令 /logs
    if message and message.strip().lower() == "/logs":
        try:
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

    # 处理 #tenant 命令
    if message and message.strip().startswith("#tenant"):
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

    # 处理文件上传
    if file is not None:
        file_path = file.name if hasattr(file, 'name') else file
        # 根据文件扩展名判断类型，调用对应工具
        ext = os.path.splitext(file_path)[1].lower()
        file_result = ""
        description = ""
        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif'):
            # 默认调用通用文字识别，但也可以通过文本指定“表格识别”
            # 这里简单处理：如果message包含“表格”，则调用表格识别，否则通用识别
            if message and "表格" in message:
                file_result = recognize_table(file_path)
                description = "（表格图片上传）请识别表格"
            else:
                file_result = ocr_image(file_path)
                description = "（图片上传）请识别文字"
        elif ext in ('.csv', '.xlsx', '.xls'):
            file_result = analyze_file(file_path)
            description = "（文件上传）请分析该文件"
        elif ext in ('.wav', '.mp3', '.m4a', '.ogg'):
            file_result = speech_to_text(file_path)
            description = "（音频上传）语音转文字"
        else:
            file_result = "不支持的文件类型"
            description = "（文件上传）"

        # 将识别/分析结果添加到聊天记录，同时保存到记忆
        history = history or []
        history.append({"role": "user", "content": description})
        history.append({"role": "assistant", "content": file_result})
        memory.append(SESSION_ID, description, file_result)
        # 返回清除文件输入框，保留文本输入框内容（以便用户追加问题）
        return history, "", None

    # 普通文本处理
    if not message or not message.strip():
        return history, "", None

    display_msg = message
    history = history or []
    history.append({"role": "user", "content": display_msg})

    answer = await chat_core(SESSION_ID, message, None)
    history.append({"role": "assistant", "content": answer})
    return history, "", None

# 租户切换处理（下拉框改变时）
def on_tenant_change(new_tenant):
    if new_tenant:
        current = memory.get_tenant(SESSION_ID)
        if new_tenant != current:
            # 保存当前历史？
            # 由于下拉框改变时无法获取当前history，这里简化处理：只切换租户，清屏由前端完成
            memory.set_tenant(SESSION_ID, new_tenant)
            # 返回空历史以清屏
            return [], new_tenant
    return gr.update(), gr.update()

# 构建界面
with gr.Blocks(title="AI 智能体", theme=gr.themes.Soft()) as demo:
    with gr.Tab("聊天"):
        gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具）")
        
        # 顶部租户切换下拉框
        with gr.Row():
            tenant_dropdown = gr.Dropdown(
                choices=get_available_tenants(),
                value="default",
                label="租户切换",
                interactive=True,
                scale=1
            )
            # 刷新按钮，用于更新租户列表
            refresh_btn = gr.Button("刷新租户列表", size="sm", scale=0)

        chatbot = gr.Chatbot(label="对话", height=500)

        # 底部输入区：文本 + 文件上传 + 音频
        with gr.Row():
            text_input = gr.Textbox(
                label="输入文字（可用 /logs 查看日志，#tenant 切换租户）",
                placeholder="在这里输入问题或指令...",
                scale=4
            )
            # 统一文件上传组件
            file_upload = gr.File(
                label="上传文件（图片/表格/CSV/Excel/音频）",
                file_types=[".csv", ".xlsx", ".xls", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".wav", ".mp3", ".m4a", ".ogg"],
                scale=1
            )
            # 音频录音按钮（保留直接录音功能）
            audio_recorder = gr.Audio(
                label="录音",
                type="filepath",
                scale=1
            )

        # 事件绑定
        # 文本输入回车
        text_input.submit(
            unified_handler,
            [text_input, chatbot, file_upload, tenant_dropdown],
            [chatbot, text_input, file_upload]
        )

        # 文件上传后自动处理
        file_upload.upload(
            unified_handler,
            [text_input, chatbot, file_upload, tenant_dropdown],
            [chatbot, text_input, file_upload]
        )

        # 音频录制完成自动处理
        audio_recorder.stop_recording(
            unified_handler,
            [text_input, chatbot, audio_recorder, tenant_dropdown],
            [chatbot, text_input, audio_recorder]
        )
        audio_recorder.upload(
            unified_handler,
            [text_input, chatbot, audio_recorder, tenant_dropdown],
            [chatbot, text_input, audio_recorder]
        )

        # 租户下拉框改变事件
        tenant_dropdown.change(
            on_tenant_change,
            [tenant_dropdown],
            [chatbot, tenant_dropdown]
        )

        # 刷新租户列表
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
    # 启动Worker后台循环（它们会在run_loop中监听Redis队列）
    loop = asyncio.get_event_loop()
    loop.create_task(query_worker.run_loop())
    loop.create_task(command_worker.run_loop())
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)