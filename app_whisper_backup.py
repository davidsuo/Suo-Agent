import gradio as gr
import whisper
import os
from main import chat_core

model = whisper.load_model("base")
SESSION_ID = "gradio_session"

def transcribe_audio(file_path):
    """
    使用 Whisper 转写音频文件。file_path 为 Gradio 音频组件 type="filepath" 返回的路径。
    """
    if not file_path or not os.path.exists(file_path):
        return "[错误] 音频文件未找到，请重新录制。"
    try:
        result = model.transcribe(file_path, language="zh", fp16=False)
        return result["text"].strip()
    except Exception as e:
        return f"[语音识别失败: {e}]"

async def handle_user_input(text, audio, history):
    """
    触发条件：音频组件值变化（录音或上传完成），或文本框回车。
    """
    user_text = text or ""
    transcribed = ""
    if audio is not None:
        transcribed = transcribe_audio(audio)

    if transcribed.startswith("[") and transcribed.endswith("]") and "失败" in transcribed or "错误" in transcribed:
        history.append({"role": "user", "content": "🎤 音频输入"})
        history.append({"role": "assistant", "content": transcribed})
        return history, "", None

    if transcribed:
        user_text = f"{user_text}。{transcribed}" if user_text else transcribed

    if not user_text.strip():
        return history, "", None

    display_msg = user_text
    if audio is not None:
        display_msg += " 🎤"
    history.append({"role": "user", "content": display_msg})

    answer = await chat_core(SESSION_ID, user_text, None)
    history.append({"role": "assistant", "content": answer})
    return history, "", None

# 界面
with gr.Blocks(title="AI 智能体", theme=gr.themes.Soft()) as demo:
    gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具 + 语音）")
    gr.Markdown("""
    **操作说明**  
    - 点击下方 **红色录制按钮** 开始说话，说完点击 **停止** → 系统自动转文字并回答。  
    - 也可直接输入文字按回车，或上传音频文件（.wav/.mp3）。
    """)

    chatbot = gr.Chatbot(label="对话", height=500)
    with gr.Row():
        text_input = gr.Textbox(label="输入文字（可选）", placeholder="在这里打字...", scale=2)
        audio_input = gr.Audio(label="录音或上传音频", type="filepath", scale=1)

    # 关键：用 change 事件，文件路径此时稳定存在
    audio_input.change(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )
    text_input.submit(
        handle_user_input,
        [text_input, audio_input, chatbot],
        [chatbot, text_input, audio_input]
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)