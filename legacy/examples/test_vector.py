import requests
import json

url = "http://127.0.0.1:10000/api/chat"
payload = {
    "session_id": "test_主对话",
    "query": "打印机报错 0x80004005 怎么处理"
}
headers = {"Content-Type": "application/json"}

# 发送流式请求
response = requests.post(url, json=payload, headers=headers, stream=True)

print("=== 后端返回的原始数据 ===")
for line in response.iter_lines():
    if line:
        decoded_line = line.decode('utf-8')
        print(decoded_line)