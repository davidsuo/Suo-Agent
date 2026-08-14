# common/feedback.py
"""
用户反馈收集模块
用于记录用户对智能体回答的满意度（👍/👎），为后续模型微调提供标注数据。
"""

import json
import os
from datetime import datetime

FEEDBACK_FILE = "feedback_log.json"

def save_feedback(session_id: str, user_query: str, assistant_response: str, feedback: str) -> None:
    """
    保存一条用户反馈。

    参数:
        session_id: 用户会话ID（通常为用户名）
        user_query: 用户当时的提问
        assistant_response: 智能体的回复
        feedback: 反馈类型，'up' 表示 👍，'down' 表示 👎
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "session_id": session_id,
        "user_query": user_query[:500],      # 截断过长内容
        "assistant_response": assistant_response[:1000],
        "feedback": feedback
    }
    try:
        # 追加写入 JSON Lines 格式
        with open(FEEDBACK_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"[反馈] 已记录 {feedback} 反馈，用户: {session_id}")
    except Exception as e:
        print(f"[反馈] 保存失败: {e}")