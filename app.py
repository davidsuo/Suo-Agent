import gradio as gr
import os
import sqlite3
from main import chat_core
from tools import speech_to_text   # 从 tools 导入百度语音转写函数

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
    answer = await chat_core(SESSION_ID, user_text, None)
    history.append({"role": "assistant", "content": answer})
    return history, "", None

# 构建界面
with gr.Blocks(title="AI 智能体") as demo:
    gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具 + 语音）")
    gr.Markdown("打字或上传音频文件，我会调用所有能力回答你。")

    chatbot = gr.Chatbot(label="对话", height=500)
    with gr.Row():
        text_input = gr.Textbox(label="输入文字（可选）", placeholder="在这里打字...", scale=2)
        audio_input = gr.Audio(label="🎤 上传音频（建议录制清晰短语音）", type="filepath", scale=1)

    # 音频上传后自动触发处理（change 事件）
    audio_input.change(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )
    # 文本框回车触发
    text_input.submit(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )

if __name__ == "__main__":
    init_database()   # 启动前检查数据库
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)