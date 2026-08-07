import gradio as gr
import pandas as pd
import os
import sqlite3
from main import chat_core
from tools import speech_to_text   # 从 tools 导入百度语音转写函数
from memory import memory   # 确保与 main.py 使用的同一个实例
import tools
import asyncio
import json
from tools import ocr_image


SESSION_ID = "render_user"

def init_database():
    """自动创建 sample.db 如果不存在"""
    db_path = "sample.db"
    if not os.path.exists(db_path):
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
    else:
        print("✅ 数据库 sample.db 已存在，无需初始化。")

# ========== 纯文本处理 ==========
async def handle_text_input(text, history):
    """只处理文本输入，忽略音频"""
    if not text or not text.strip():
        return history, ""
    
    # 在 /logs 命令处理之前
    if text.strip().startswith("#tenant "):
        tenant = text.strip()[8:].strip()
        memory.set_tenant(SESSION_ID, tenant)   # 需要 memory 支持
        return history, f"已切换到租户: {tenant}"

    # 特殊命令：查看日志
    if text.strip().lower() == "/logs":
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
        return history, ""
    
    # 处理 #tenant 命令
    if text.strip().startswith("#tenant"):
        parts = text.strip().split(maxsplit=1)
        if len(parts) == 1:
            current_tenant = memory.get_tenant(SESSION_ID)
            answer = f"当前租户：{current_tenant}"
            # 不切换，不清屏，返回原历史
            history = history or []
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": answer})
            return history, ""
        else:
            new_tenant = parts[1].strip()
            memory.set_tenant(SESSION_ID, new_tenant)
            answer = f"已切换到租户：{new_tenant}。会话已清空，您可以开始新的对话。"
            # 返回一个全新的历史，仅包含本次切换消息，实现清屏
            new_history = [
                {"role": "assistant", "content": answer}
            ]
            return new_history, ""


# ========== 音频处理 ==========
async def handle_audio_input(audio, history):
    """处理音频输入，转写文字后自动发送"""
    if audio is None:
        return history, "", None

    from tools import speech_to_text
    transcribed = speech_to_text(audio)
    if transcribed.startswith("语音识别失败"):
        history = history or []
        history.append({"role": "user", "content": "🎤 音频输入"})
        history.append({"role": "assistant", "content": transcribed})
        return history, "", None

    # 转写成功，作为文本提交
    user_text = transcribed
    display_msg = user_text + " 🎤"
    history = history or []
    history.append({"role": "user", "content": display_msg})

    answer = await chat_core(SESSION_ID, user_text, None)
    history.append({"role": "assistant", "content": answer})
    return history, "", None

# 构建界面
with gr.Blocks(title="AI 智能体") as demo:
    with gr.Tab("聊天"):
        gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具 + 语音 + 文件分析）")
        gr.Markdown("上传 CSV/Excel 文件、打字或上传音频，我会调用所有能力回答你。")

        chatbot = gr.Chatbot(label="对话", height=500)

        # ... 其他组件 ...
        general_image_input = gr.Image(label="🖼️ 上传图片识别文字", type="filepath")
        table_image_input = gr.Image(label="📊 上传表格图片进行识别（CSV）", type="filepath")
        file_input = gr.File(label="📁 上传 CSV 或 Excel 文件", file_types=[".csv", ".xlsx", ".xls"])
        # ...

        with gr.Row():
            text_input = gr.Textbox(label="输入文字（可选）", placeholder="在这里打字...", scale=2)
            audio_input = gr.Audio(label="🎤 上传音频", type="filepath", scale=1)
        
        async def handle_general_image_upload(image_path, history):
            """上传普通图片，调用通用文字识别"""
            if image_path is None:
                return history
            ocr_result = tools.ocr_image(image_path)
            history = history or []
            history.append({"role": "user", "content": "（图片上传）请识别文字"})
            history.append({"role": "assistant", "content": f"识别结果：\n{ocr_result}"})
            memory.append(SESSION_ID, "（图片上传）请识别文字", ocr_result)
            return history
        general_image_input.upload(
            handle_general_image_upload,
            [general_image_input, chatbot],
            [chatbot]
        )
    
        async def handle_table_image_upload(image_path, history):
            """上传表格图片，自动调用表格识别并返回 CSV"""
            if image_path is None:
                return history
            ocr_result = tools.recognize_table(image_path)
            history = history or []
            history.append({"role": "user", "content": "（表格图片上传）请识别表格"})
            history.append({"role": "assistant", "content": f"表格识别结果（CSV）：\n{ocr_result}"})
            memory.append(SESSION_ID, "（表格图片上传）请识别表格", ocr_result)
            return history
        table_image_input.upload(
            handle_table_image_upload,
            [table_image_input, chatbot],
            [chatbot]
        )
        async def handle_file_upload(file, history):
            if file is None:
                return history, "", gr.update(value=None)

            try:
                # 尝试分析文件
                analysis_result = tools.analyze_file(file.name)
                # 记录最近文件路径
                tools.last_uploaded_file = file.name
                # 构建用户消息
                history = history or []
                history.append({"role": "user", "content": "（文件上传）请分析该文件"})
                history.append({"role": "assistant", "content": analysis_result})
                # 写入记忆
                memory.append(SESSION_ID, "（文件上传）请分析该文件", analysis_result)
            except Exception as e:
                # 如果分析失败，返回错误信息
                history = history or []
                history.append({"role": "user", "content": "（文件上传）"})
                history.append({"role": "assistant", "content": f"文件分析失败：{str(e)}"})
                # 即使失败也尝试清空文件组件
                return history, "", gr.update(value=None)

            return history, "", gr.update(value=None)

        # 在界面构建部分
        file_input.upload(
            handle_file_upload,
            [file_input, chatbot],
            [chatbot, text_input, file_input]
        )

        # 原有的文本和音频处理保持不变（注意需要适配多输入）
        async def handle_user_input(text, audio, history):
            transcribed = ""
            if text and text.strip():
                audio = None
            user_text = text or ""
            if audio is not None:
                from tools import speech_to_text
                transcribed = speech_to_text(audio)
                if not transcribed.startswith("语音识别失败"):
                    user_text = transcribed
            else:
                history = history or []
                history.append({"role": "user", "content": "🎤 音频输入"})
                history.append({"role": "assistant", "content": transcribed})
                return history, "", None

            if not user_text.strip():
                return history, "", None

            display_msg = user_text
            if audio is not None:
                display_msg += " 🎤"
            history = history or []
            history.append({"role": "user", "content": display_msg})

            answer = await chat_core(SESSION_ID, user_text, None)
            history.append({"role": "assistant", "content": answer})
            return history, "", None
            
    with gr.Tab("Worker 监控"):
        gr.Markdown("## 实时 Worker 状态")
        refresh_btn = gr.Button("刷新")
        status_table = gr.Dataframe(headers=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度"], interactive=False)

        def refresh_status():
            from main import get_workers_status
            stats = get_workers_status()
            data = []
            for s in stats:
                data.append([s["name"], str(s["is_running"]), s["task_count"], s["error_count"], s["queue_size"]])
            return pd.DataFrame(data, columns=["Worker名称", "运行中", "完成任务", "失败任务", "队列长度"])

        refresh_btn.click(fn=refresh_status, outputs=status_table)
        # 页面加载时自动刷新一次
        status_table.value = refresh_status()

    # 绑定事件
    text_input.submit(
        handle_text_input,
        [text_input, chatbot],
        [chatbot, text_input]
    )

    audio_input.change(
        handle_audio_input,
        [audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )

if __name__ == "__main__":
    init_database()   # 启动前检查数据库
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)