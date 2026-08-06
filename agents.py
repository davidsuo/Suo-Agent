# agents.py
import asyncio
import uuid
import traceback
import json
from functools import partial
from typing import Any, Dict, Callable

class Agent:
    def __init__(self, name: str, event_bus: 'EventBus'):
        self.name = name
        self.bus = event_bus
        self.queue = asyncio.Queue()
        self.is_running = False
        self.task_count = 0
        self.error_count = 0

    async def send_task(self, task):
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        return await future

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_loop(self):
        self.bus.subscribe(f"ToolRequested.{self.name}", self._on_tool_requested)
        self.is_running = True
        while self.is_running:
            await asyncio.sleep(3600)

    async def _on_tool_requested(self, event_data):
        task = event_data.get("task")
        future = event_data.get("future")
        if not task or not future:
            return
        try:
            result = await self.handle_task(task)
            self.task_count += 1
        except Exception as e:
            self.error_count += 1
            result = {"error": str(e), "traceback": traceback.format_exc()}
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
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, event_bus)
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
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, tools, event_bus)
        self.cache = {}

    def _get_cache_key(self, tool_name, arguments):
        return f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        if tool_name == "get_current_time":
            return await super().handle_task(task)

        cache_key = self._get_cache_key(tool_name, arguments)
        if cache_key in self.cache:
            print(f"[QueryWorker] 缓存命中: {cache_key}", flush=True)
            return {"result": self.cache[cache_key]}

        result = await super().handle_task(task)
        if "result" in result and "error" not in result:
            self.cache[cache_key] = result["result"]
            print(f"[QueryWorker] 缓存写入: {cache_key}", flush=True)
        return result