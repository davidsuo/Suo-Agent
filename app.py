import gradio as gr
import os
from main import chat_core
from main import init_database   # 如果 init_database 在 main.py 中定义


# 固定会话 ID（可后续改为多会话）
SESSION_ID = "render_user"

async def respond(message, history):
    """纯文本对话接口"""
    answer = await chat_core(SESSION_ID, message, None)
    return answer

# 构建界面
with gr.Blocks(title="AI 智能体") as demo:
    gr.Markdown("# 🤖 AI 智能体（记忆 + 知识库 + 工具）")
    gr.Markdown("直接在下方输入文字，我会调用所有工具和知识库回答你。")

    chatbot = gr.Chatbot(label="对话", height=500)
    text_input = gr.Textbox(label="输入你的问题", placeholder="在这里打字...")
    send_btn = gr.Button("发送")

    async def on_send(text, history):
        if not text.strip():
            return history, ""
        history = history or []
        history.append({"role": "user", "content": text})
        bot_response = await respond(text, history)
        history.append({"role": "assistant", "content": bot_response})
        return history, ""

    send_btn.click(on_send, [text_input, chatbot], [chatbot, text_input])
    text_input.submit(on_send, [text_input, chatbot], [chatbot, text_input])

if __name__ == "__main__":
    # Render 会通过 PORT 环境变量指定端口
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)