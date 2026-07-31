import gradio as gr
import os
import sqlite3
from main import chat_core   # 只导入聊天核心，不依赖 init_database

SESSION_ID = "render_user"

def init_database():
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

async def respond(message, history):
    answer = await chat_core(SESSION_ID, message, None)
    return answer

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
    init_database()   # 先初始化数据库
    port = int(os.environ.get("PORT", 7860))
    demo.launch(server_name="0.0.0.0", server_port=port)