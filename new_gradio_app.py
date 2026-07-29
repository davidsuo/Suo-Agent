import gradio as gr
import whisper
import os
from add_core_main import chat_core

model = whisper.load_model("base")
SESSION_ID = "voice_final"

def transcribe_and_reply(audio_path):
    if not audio_path or not os.path.exists(audio_path):
        return "错误：音频文件无效，请重新上传。"
    try:
        result = model.transcribe(audio_path, language="zh", fp16=False)
        text = result["text"].strip()
    except Exception as e:
        return f"语音识别失败: {e}"
    if not text:
        return "未检测到语音内容，请检查录音是否正常。"
    
    # 调用智能体（需要用 asyncio 运行异步函数）
    import asyncio
    answer = asyncio.run(chat_core(SESSION_ID, text, None))
    return f"【您说】{text}\n\n【智能体】{answer}"

with gr.Blocks(title="语音助手") as demo:
    gr.Markdown("# 🎤 语音问答（上传音频文件）")
    gr.Markdown("""
    **使用步骤**  
    1. 用手机或电脑录音机录下你的问题，保存为 .wav 或 .mp3。  
    2. 点击下方区域上传该音频文件。  
    3. 稍等片刻，下方会显示识别出的文字和智能体的回答。
    """)
    audio = gr.Audio(label="上传音频文件", type="filepath")
    output = gr.Textbox(label="结果", lines=6)
    audio.change(fn=transcribe_and_reply, inputs=audio, outputs=output)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)