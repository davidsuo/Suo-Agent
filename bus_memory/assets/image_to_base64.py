import base64

def image_to_base64(image_path):
    with open(image_path, "rb") as f:
        data = f.read()
    # 注意：这里必须是标准英文连字符的 utf-8
    return base64.b64encode(data).decode("utf-8")

# 替换成你的logo图片路径（在当前目录下直接写文件名即可）
b64_str = image_to_base64("logo.png")

# 如果要HTML直接用，带上前缀：
html_img = f"data:image/png;base64,{b64_str}"

# 打印出可以直接粘贴的完整 HTML 代码
print(html_img)