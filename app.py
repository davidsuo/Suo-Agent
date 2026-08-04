import gradio as gr
import os
import sqlite3
from main import chat_core
from tools import speech_to_text   # 从 tools 导入百度语音转写函数
from memory import memory   # 确保与 main.py 使用的同一个实例
import tools
import asyncio
import json


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

async def handle_user_input(text, audio, history):
    user_text = text or ""
    
    # 特殊命令：查看规划日志
    if text.strip().lower() == "/logs":
        try:
            with open("plan_log.json", "r", encoding="utf-8") as f:
                lines = f.readlines()
            if not lines:
                answer = "暂无规划日志。"
            else:
                recent = lines[-3:] if len(lines) > 3 else lines
                logs_display = ""
                for idx, line in enumerate(recent, 1):
                    entry = json.loads(line)
                    logs_display += f"**记录 {idx}** (时间: {entry['timestamp']})\n"
                    logs_display += f"用户需求: {entry['user_query']}\n"
                    logs_display += f"计划步骤: {len(entry['plan'])} 步\n"
                    # 简单展示结果摘要，避免过长
                    for step_id, result in entry['results'].items():
                        result_str = str(result)[:200]
                        logs_display += f"  步骤{step_id}: {result_str}...\n"
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

    # 如果有音频，先转成文字
    if audio is not None:
        transcribed = speech_to_text(audio)
        if not transcribed.startswith("语音识别失败"):
            user_text = transcribed
        else:
            # 转录失败，返回错误信息
            history = history or []
            history.append({"role": "user", "content": "🎤 音频输入"})
            history.append({"role": "assistant", "content": transcribed})
            return history, "", None

    if not user_text.strip():
        return history, "", None

    # 显示用户消息（带语音标记）
    display_msg = user_text
    if audio is not None:
        display_msg += " 🎤"
    history = history or []
    history.append({"role": "user", "content": display_msg})

    # 调用智能体核心
    try:
        answer = await asyncio.wait_for(chat_core(SESSION_ID, user_text, None), timeout=60)
    except asyncio.TimeoutError:
        answer = "请求超时，请稍后重试或简化您的问题。"
    history.append({"role": "assistant", "content": answer})
    return history, "", None

# 构建界面
with gr.Blocks(title="AI 智能体") as demo:
    gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具 + 语音 + 文件分析）")
    gr.Markdown("上传 CSV/Excel 文件、打字或上传音频，我会调用所有能力回答你。")

    chatbot = gr.Chatbot(label="对话", height=500)

    # ... 其他组件 ...
    file_input = gr.File(label="📁 上传 CSV 或 Excel 文件", file_types=[".csv", ".xlsx", ".xls"])
    # ...

    with gr.Row():
        text_input = gr.Textbox(label="输入文字（可选）", placeholder="在这里打字...", scale=2)
        audio_input = gr.Audio(label="🎤 上传音频", type="filepath", scale=1)

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

    # 绑定事件
    text_input.submit(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )
    audio_input.change(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )

if __name__ == "__main__":
    init_database()   # 启动前检查数据库
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)