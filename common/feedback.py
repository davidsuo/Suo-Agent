# common/feedback.py
"""
用户反馈收集模块

用于记录用户对智能体回答的满意度（👍/👎），
为后续模型微调和体验优化提供标注数据。
"""

import json
import threading
from datetime import datetime
from typing import Optional

FEEDBACK_FILE = "feedback_log.json"

# 截断长度常量，防止单条记录过大
MAX_QUERY_LENGTH = 500
MAX_RESPONSE_LENGTH = 1000

# 允许的反馈类型
VALID_FEEDBACK = {"up", "down"}

# 线程锁，确保多用户同时提交反馈时不会交叉写入
_feedback_lock = threading.Lock()

def save_feedback(
    session_id: str,
    user_query: str,
    assistant_response: str,
    feedback: str,
) -> None:
    """
    保存一条用户反馈到 JSON Lines 文件。

    参数:
        session_id: 用户会话ID（通常为用户名）
        user_query: 用户当时的提问
        assistant_response: 智能体的回复
        feedback: 反馈类型，'up' 表示 👍，'down' 表示 👎

    返回:
        无（None）。写入成功或失败仅通过打印日志提示，不抛出异常。
    """
    # 校验 feedback 参数
    if feedback not in VALID_FEEDBACK:
        print(f"[反馈] 无效的反馈类型: {feedback}，忽略")
        return

    # 截断过长内容
    query_clean = user_query[:MAX_QUERY_LENGTH]
    response_clean = assistant_response[:MAX_RESPONSE_LENGTH]

    entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "user_query": query_clean,
        "assistant_response": response_clean,
        "feedback": feedback,
    }

    try:
        # 使用锁保护追加写入，避免多用户并发导致文件格式损坏
        with _feedback_lock:
            with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[反馈] 已记录 {feedback} 反馈，用户: {session_id}")
    except Exception as e:
        print(f"[反馈] 保存失败: {e}")