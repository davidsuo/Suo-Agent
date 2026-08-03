# agents.py
import asyncio
import uuid
import traceback
from typing import Any, Dict, Callable
from functools import partial


class Agent:
    def __init__(self, name: str):
        self.name = name
        self.queue = asyncio.Queue()
        self.is_running = False   # 惰性启动标志

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """发送任务到该智能体，并等待结果返回"""
        task_id = str(uuid.uuid4())
        task["task_id"] = task_id
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        result = await future
        return result

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """子类必须实现，处理具体任务"""
        raise NotImplementedError

    async def run_loop(self):
        """智能体主循环，从队列中取任务并处理"""
        self.is_running = True
        while True:
            task, future = await self.queue.get()
            try:
                result = await self.handle_task(task)
            except Exception as e:
                result = {"error": str(e), "traceback": traceback.format_exc()}
            future.set_result(result)

class WorkerAgent(Agent):
    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        try:
            func = self.tools[tool_name]
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, partial(func, **arguments))
            return {"result": result}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}
            