# common/health.py
"""
系统健康数据聚合模块

从日志文件（plan_log.json、feedback_log.json）中提取关键运行指标，
供系统健康仪表板使用。该模块独立于其他组件，仅读取日志文件，
不修改任何状态。
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

LOG_FILE = "plan_log.json"
FEEDBACK_FILE = "feedback_log.json"

# 失败状态集合（用于统计失败任务）
FAILED_STATUSES = {"failed", "timeout", "error", "failed_with_compensation"}

def _read_json_lines(file_path: str) -> List[Dict]:
    """
    读取 JSON Lines 格式的文件，返回有效的字典列表。

    参数:
        file_path: 日志文件路径

    返回:
        包含所有有效记录的列表，如果文件不存在或损坏，返回空列表。
    """
    entries = []
    if not os.path.exists(file_path):
        return entries
    try:
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
                    # 忽略损坏的行，不影响其余数据
                    continue
    except Exception as e:
        print(f"[Health] 读取日志失败 {file_path}: {e}")
    return entries

def _is_success(status: str) -> bool:
    """判断状态是否为成功"""
    return status == "success"

def _is_failed(status: str) -> bool:
    """判断状态是否为失败"""
    return status in FAILED_STATUSES

def get_system_health() -> Dict[str, Any]:
    """
    聚合系统健康指标。

    返回:
        包含以下键的字典：
        - total_tasks: 总任务数
        - success_tasks: 成功任务数
        - failed_tasks: 失败任务数
        - success_rate: 成功率（0-100，保留一位小数）
        - active_users: 活跃用户数（最近24小时有操作）
        - total_users: 总用户数（出现在日志中）
        - total_feedback: 总反馈数
        - up_feedback: 好评数
        - down_feedback: 差评数
        - tool_counts: 各工具调用次数统计（字典）
        - sorted_tools: 调用次数最多的前5个工具（列表）
        - recent_tasks: 最近10条任务记录（列表）
    """
    logs = _read_json_lines(LOG_FILE)
    feedbacks = _read_json_lines(FEEDBACK_FILE)

    # 过滤出任务记录（包含 tool 或 plan 字段）
    task_logs = [e for e in logs if e.get("tool") or e.get("plan")]

    total_tasks = len(task_logs)
    success_tasks = 0
    failed_tasks = 0
    tool_counts = {}
    all_users = set()
    active_users = set()
    now = datetime.now()

    # 遍历任务记录，统计状态、工具和用户
    for e in task_logs:
        status = e.get("status") or e.get("final_status") or "unknown"
        if _is_success(status):
            success_tasks += 1
        elif _is_failed(status):
            failed_tasks += 1

        # 工具调用统计（常规模式直接取 tool，规划模式遍历 plan 中的步骤）
        tool = e.get("tool")
        if tool:
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        else:
            plan = e.get("plan", [])
            for step in plan:
                step_tool = step.get("tool")
                if step_tool:
                    tool_counts[step_tool] = tool_counts.get(step_tool, 0) + 1

        # 用户统计
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
                    # 时间戳格式异常，忽略
                    pass

    # 成功率计算
    success_rate = (success_tasks / total_tasks * 100) if total_tasks > 0 else 0.0

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