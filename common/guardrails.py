# common/guardrails.py
"""
安全护栏模块

职责：
1. 输入过滤：阻止敏感词、越狱指令和过长输入
2. 输出脱敏：隐藏常见隐私信息（邮箱、手机号、身份证号等）
3. 工具调用确认：对危险工具（如发邮件）要求二次确认
"""

import re
from typing import Tuple

# ==================== 配置区 ====================
# 敏感词与越狱检测关键词（可扩展）
BLOCKED_KEYWORDS = [
    "忽略所有指令", "忽略之前", "你是我的奴隶",
    "查看system prompt", "show system prompt",
    "ignore all instructions", "你是一个",
    "你叫什么名字", "你是什么模型",
]

MAX_QUERY_LENGTH = 2000  # 最大输入长度（字符）

# 需要二次确认的危险工具
DANGEROUS_TOOLS = ["send_email"]

# 输出脱敏规则（正则表达式，按顺序应用）
SENSITIVE_PATTERNS = {
    "email": re.compile(r'[\w\.-]+@[\w\.-]+\.\w+'),
    "phone": re.compile(r'1[3-9]\d{9}'),
    "id_card": re.compile(r'\b\d{17}[\dXx]\b'),  # 简单身份证号
}

# ==================== 输入护栏 ====================
def input_guard(query: str) -> Tuple[bool, str]:
    """
    检查用户输入是否安全。

    参数:
        query: 用户输入的文本

    返回:
        (是否安全, 错误信息) 二元组。安全时返回 (True, "")。
    """
    if not query or not query.strip():
        return False, "输入不能为空。"

    if len(query) > MAX_QUERY_LENGTH:
        return False, f"输入过长，请限制在 {MAX_QUERY_LENGTH} 字符以内。"

    lower_query = query.lower()
    for keyword in BLOCKED_KEYWORDS:
        if keyword.lower() in lower_query:
            return False, "您的输入包含不受支持的内容，请重新描述。"

    return True, ""

# ==================== 工具调用护栏 ====================
def tool_call_guard(tool_name: str) -> bool:
    """
    判断指定工具是否需要用户二次确认。

    返回 True 表示需要确认，False 表示可直接执行。
    """
    return tool_name in DANGEROUS_TOOLS

# ==================== 输出护栏 ====================
def output_guard(text: str) -> str:
    """
    对智能体输出进行脱敏处理，隐藏常见敏感信息。

    参数:
        text: 智能体的原始回复

    返回:
        脱敏后的文本。
    """
    if not text:
        return text

    # 按照规则替换敏感信息
    text = SENSITIVE_PATTERNS["email"].sub('[邮箱已隐藏]', text)
    text = SENSITIVE_PATTERNS["phone"].sub('[手机号已隐藏]', text)
    text = SENSITIVE_PATTERNS["id_card"].sub('[身份证号已隐藏]', text)

    return text