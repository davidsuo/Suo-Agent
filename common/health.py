# common/health.py
"""
系统健康数据聚合模块
从日志文件（plan_log.json、feedback_log.json）和 Worker 统计中提取关键指标，
供健康仪表板使用。
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any

LOG_FILE = "plan_log.json"
FEEDBACK_FILE = "feedback_log.json"

def _read_json_lines(file_path: str) -> List[Dict]:
    """读取 JSON Lines 文件，返回有效字典列表"""
    entries = []
    if not os.path.exists(file_path):
        return entries
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if isinstance(entry, dict):
                    entries.append(entry)
            except json.JSONDecodeError:
                continue
    return entries

def get_system_health() -> Dict[str, Any]:
    """
    聚合系统健康指标。

    返回字典包含：
    - total_tasks: 总任务数
    - success_tasks: 成功任务数
    - failed_tasks: 失败任务数
    - success_rate: 成功率（0-100）
    - active_users: 活跃用户数（最近24小时有操作的用户）
    - total_users: 总用户数（出现在日志中的用户）
    - total_feedback: 总反馈数
    - up_feedback: 好评数
    - down_feedback: 差评数
    - tool_stats: 工具调用统计（按工具名分组）
    - recent_tasks: 最近10条任务记录
    """
    logs = _read_json_lines(LOG_FILE)
    feedbacks = _read_json_lines(FEEDBACK_FILE)

    # 过滤掉非任务记录（如某些特殊条目）
    task_logs = [e for e in logs if e.get("tool") or e.get("plan")]

    total_tasks = len(task_logs)
    success_tasks = 0
    failed_tasks = 0
    tool_counts = {}

    for e in task_logs:
        # 确定状态
        status = e.get("status") or e.get("final_status") or "unknown"
        if status == "success":
            success_tasks += 1
        elif status in ("failed", "timeout", "error", "failed_with_compensation"):
            failed_tasks += 1

        # 工具统计
        tool = e.get("tool")
        if tool:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        else:
            # 规划模式，可能包含多个步骤
            plan = e.get("plan", [])
            for step in plan:
                step_tool = step.get("tool")
                if step_tool:
                    tool_counts[step_tool] = tool_counts.get(step_tool, 0) + 1

    success_rate = (success_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

    # 活跃用户（最近24小时）
    now = datetime.now()
    active_users = set()
    all_users = set()
    for e in logs:
        username = e.get("username") or e.get("session_id")
        if username:
            all_users.add(username)
            ts_str = e.get("timestamp")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if now - ts <= timedelta(hours=24):
                        active_users.add(username)
                except ValueError:
                    pass

    # 反馈统计
    up_feedback = sum(1 for f in feedbacks if f.get("feedback") == "up")
    down_feedback = sum(1 for f in feedbacks if f.get("feedback") == "down")

    # 工具调用排行（前5）
    sorted_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # 最近任务记录
    recent_tasks = task_logs[-10:] if task_logs else []

    return {
        "total_tasks": total_tasks,
        "success_tasks": success_tasks,
        "failed_tasks": failed_tasks,
        "success_rate": round(success_rate, 1),
        "active_users": len(active_users),
        "total_users": len(all_users),
        "total_feedback": len(feedbacks),
        "up_feedback": up_feedback,
        "down_feedback": down_feedback,
        "tool_counts": tool_counts,
        "sorted_tools": sorted_tools,
        "recent_tasks": recent_tasks,
    }