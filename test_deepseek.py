import os
from openai import OpenAI
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url="https://api.deepseek.com"
)

try:
    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=5
    )
    print("✅ API 调用成功")
    print(f"响应: {response.choices[0].message.content}")
except Exception as e:
    print(f"❌ API 调用失败: {type(e).__name__}: {e}")