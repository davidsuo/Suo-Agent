# guardrails.py
import re

# guardrails.py 增加以下函数

def build_confirm_message(tool_name: str, arguments: dict, user_query: str) -> str:
    """生成确认请求文本"""
    args_summary = ", ".join(f"{k}={v}" for k, v in arguments.items())
    return f"[CONFIRM]{tool_name}|{args_summary}|{user_query}"

def parse_confirm_message(text: str) -> dict | None:
    """如果是确认请求，返回解析后的字典；否则返回 None"""
    if text.startswith("[CONFIRM]"):
        parts = text[len("[CONFIRM]"):].split("|")
        if len(parts) == 3:
            return {
                "tool_name": parts[0],
                "args_summary": parts[1],
                "user_query": parts[2]
            }
    return None

def is_confirmed(text: str) -> bool:
    """检查是否为已确认的消息"""
    return text.startswith("[CONFIRMED]")



# ----------------- 输入护栏 -----------------
BLOCKED_KEYWORDS = [
    "忽略所有指令", "忽略之前", "你是我的奴隶", "查看system prompt",
    "show system prompt", "ignore all instructions", "你是一个",
    "你叫什么名字", "你是什么模型"  # 示例，可自行增删
]

MAX_QUERY_LENGTH = 2000  # 字符

def input_guard(query: str) -> tuple[bool, str]:
    """
    返回 (是否安全, 错误信息)
    安全时返回 (True, "")，不安全返回 (False, 拦截原因)
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

# ----------------- 工具调用护栏 -----------------
DANGEROUS_TOOLS = ["send_email"]  # 需要二次确认的工具

def tool_call_guard(tool_name: str) -> bool:
    """返回 True 表示需要用户确认"""
    return tool_name in DANGEROUS_TOOLS

# ----------------- 输出护栏 -----------------
# 简单的脱敏规则：隐藏邮箱和11位手机号
EMAIL_PATTERN = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
PHONE_PATTERN = re.compile(r'1[3-9]\d{9}')

def output_guard(text: str) -> str:
    """对输出进行脱敏处理"""
    text = EMAIL_PATTERN.sub('[邮箱已隐藏]', text)
    text = PHONE_PATTERN.sub('[手机号已隐藏]', text)
    return text