import gradio as gr
import whisper
import os
from add_core_main import chat_core  # 复用你之前的全部后端逻辑

model = whisper.load_model("base")
SESSION_ID = "voice_user"

def transcribe_and_chat(audio_path):
    """处理上传的音频文件：转录 + 智能体回答"""
    if not audio_path or not os.path.exists(audio_path):
        return "错误：未找到音频文件，请重新上传。"
    
    # 1. 语音转文字
    try:
        result = model.transcribe(audio_path, language="zh", fp16=False)
        text = result["text"].strip()
    except Exception as e:
        return f"语音识别失败: {e}"
    
    if not text:
        return "未检测到有效语音，请重新录制并上传。"
    
    # 2. 调用你的智能体核心（异步包装）
    import asyncio
    answer = asyncio.run(chat_core(SESSION_ID, text, None))
    return f"【您说】{text}\n\n【智能体】{answer}"

# 极简界面：只有一个上传框 + 显示区域
with gr.Blocks(title="语音助手") as demo:
    gr.Markdown("# 🎤 语音问答（上传音频文件）")
    gr.Markdown("""
    **使用方法**  
    1. 用手机或电脑录音机录制一段话（问一个问题），保存为 WAV 或 MP3。  
    2. 点击下方上传区域，选择音频文件。  
    3. 等待几秒，看到转写结果和智能体回答。
    """)
    audio_input = gr.Audio(label="上传音频文件", type="filepath")
    output_text = gr.Textbox(label="处理结果", lines=5)
    audio_input.change(fn=transcribe_and_chat, inputs=audio_input, outputs=output_text)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7870)