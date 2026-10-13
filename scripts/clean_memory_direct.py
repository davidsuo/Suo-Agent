# scripts/clean_memory_direct.py
"""
最直接的方式：对 memory.json 全文做字符串替换，不解析 JSON。
用于处理 history 结构不确定 / 脚本遍历失效的情况。
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_FILE = os.path.join(BASE_DIR, "uploads", "memory.json")

REPLACE_MAP = [
    ("[手机号已隐藏]", "REDACTED"),
    ("[邮箱已隐藏]", "REDACTED"),
    ("[身份证号已隐藏]", "REDACTED"),
]


def main():
    if not os.path.exists(MEMORY_FILE):
        print(f"未找到 {MEMORY_FILE}")
        return

    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        text = f.read()

    original_size = len(text)
    print(f"文件: {MEMORY_FILE}")
    print(f"原文件大小: {original_size} 字符")

    for bad, good in REPLACE_MAP:
        n = text.count(bad)
        if n > 0:
            print(f"  发现 {n} 处 '{bad}' → 替换为 '{good}'")
        text = text.replace(bad, good)

    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"新文件大小: {len(text)} 字符")

    # 复查
    for bad, _ in REPLACE_MAP:
        if bad in text:
            print(f"⚠️ 仍有残留: {bad}")
        else:
            print(f"✅ 已清空: {bad}")


if __name__ == "__main__":
    main()