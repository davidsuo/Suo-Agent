from PIL import Image

def png_to_ico(png_input: str, ico_output: str):
    # Windows推荐标准尺寸集合
    icon_sizes = [(16,16), (32,32), (48,48), (128,128), (256,256)]
    img = Image.open(png_input).convert("RGBA")

    icon_frames = []
    for sz in icon_sizes:
        resized = img.resize(sz, Image.Resampling.LANCZOS)
        icon_frames.append(resized)

    # 保存为ico，把全部尺寸打包进同一个文件
    icon_frames[0].save(
        ico_output,
        format="ICO",
        sizes=icon_sizes,
        append_images=icon_frames[1:]
    )
    print(f"转换完成，输出：{ico_output}")

# 使用示例，修改你的文件路径
if __name__ == "__main__":
    png_to_ico("logo.png", "favicon.ico")