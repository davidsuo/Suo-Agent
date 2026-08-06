# agents.py
import asyncio
import uuid
import traceback
import json
from functools import partial
from typing import Any, Dict, Callable

class Agent:
    def __init__(self, name: str):
        self.name = name
        self.queue = asyncio.Queue()
        self.is_running = False
        self.task_count = 0
        self.error_count = 0

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(uuid.uuid4())
        task["task_id"] = task_id
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        result = await future
        return result

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_loop(self):
        print(f"[{self.name}] Worker 启动，等待任务...")
        self.is_running = True
        while True:
            task, future = await self.queue.get()
            print(f"[{self.name}] 收到任务: {task.get('tool', 'unknown')}")
            try:
                result = await self.handle_task(task)
                self.task_count += 1
                print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count})")
            except Exception as e:
                self.error_count += 1
                result = {"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}
                print(f"[{self.name}] 任务执行异常: {e}")
            future.set_result(result)

    def get_stats(self):
        return {
            "name": self.name,
            "is_running": self.is_running,
            "task_count": self.task_count,
            "error_count": self.error_count,
            "queue_size": self.queue.qsize()
        }

class WorkerAgent(Agent):
    def __init__(self, name: str, tools: Dict[str, Callable]):
        super().__init__(name)
        self.tools = tools

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        func = self.tools[tool_name]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(func, **arguments))
        return {"result": result}

class QueryWorker(WorkerAgent):
    """带缓存的查询 Worker（时间查询禁用缓存）"""
    def __init__(self, name: str, tools: Dict[str, Callable]):
        super().__init__(name, tools)
        self.cache = {}

    def _get_cache_key(self, tool_name, arguments):
        return f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        # 时间查询不缓存
        if tool_name == "get_current_time":
            return await super().handle_task(task)

        cache_key = self._get_cache_key(tool_name, arguments)
        if cache_key in self.cache:
            print(f"[QueryWorker] 缓存命中: {cache_key}", flush=True)
            return {"result": self.cache[cache_key]}

        result = await super().handle_task(task)
        # 成功才缓存，且确保 result 是成功格式
        if "result" in result and "error" not in result:
            self.cache[cache_key] = result["result"]
            print(f"[QueryWorker] 缓存写入: {cache_key}", flush=True)
        return result