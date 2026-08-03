# agents.py
import asyncio
import uuid
import traceback
from functools import partial
from typing import Any, Dict, Callable

class Agent:
    def __init__(self, name: str):
        self.name = name
        self.queue = asyncio.Queue()
        self.is_running = False
        self.task_count = 0      # 完成任务数
        self.error_count = 0     # 失败任务数


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
                self.task_count += 1
            except Exception as e:
                self.error_count += 1
                result = {"error": str(e), "traceback": traceback.format_exc()}
                print(f"[{self.name}] 任务执行失败: {e}")
            future.set_result(result)
            print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count})")

class WorkerAgent(Agent):
    """通用执行智能体，可调用多种工具"""
    def __init__(self, name: str, tools: Dict[str, Callable]):
        super().__init__(name)
        self.tools = tools

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        try:
            func = self.tools[tool_name]
            loop = asyncio.get_event_loop()
            # 使用 partial 传递关键字参数，在线程池中执行同步函数
            result = await loop.run_in_executor(None, partial(func, **arguments))
            return {"result": result}
        except Exception as e:
            return {"error": str(e), "traceback": traceback.format_exc()}
            
# ---------- 专业智能体子类（实际仍继承 WorkerAgent，只是职责明确） ----------
class SearchWorker(WorkerAgent):
    """负责网页搜索和语音转文字"""
    pass

class CodeWorker(WorkerAgent):
    """负责 Python 代码执行"""
    pass

class DataWorker(WorkerAgent):
    """负责数据库查询和文件分析"""
    pass