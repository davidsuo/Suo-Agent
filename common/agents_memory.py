# common/agents_memory.py
import asyncio
import json
import traceback
import time
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
        self.total_time = 0.0   # 总耗时（秒）

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        return await future

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

async def run_loop(self):
    print(f"[{self.name}] Worker 启动（内存总线），等待任务...")
    self.is_running = True
    while True:
        task, future = await self.queue.get()
        print(f"[{self.name}] 收到任务: {task.get('tool', 'unknown')}")
        start_time = time.monotonic()
        try:
            result = await self.handle_task(task)
            self.task_count += 1
        except Exception as e:
            self.error_count += 1
            result = {"error": str(e), "traceback": traceback.format_exc()}
            print(f"[{self.name}] 任务执行失败: {e}")
        elapsed = time.monotonic() - start_time
        self.total_time += elapsed
        future.set_result(result)
        print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count}, 耗时: {elapsed:.2f}s)")

def get_stats(self):
    total = self.task_count + self.error_count
    avg_time = (self.total_time / total) if total > 0 else 0.0
    error_rate = (self.error_count / total) if total > 0 else 0.0
    return {
        "name": self.name,
        "is_running": self.is_running,
        "task_count": self.task_count,
        "error_count": self.error_count,
        "queue_size": self.queue.qsize(),
        "avg_time": round(avg_time, 2),
        "error_rate": round(error_rate, 2),
    }

class WorkerAgent(Agent):
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, event_bus)
        self.tools = tools

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})
        # 保留日程等工具的 _tenant 参数
        if tool_name not in ("add_event", "list_events", "delete_event"):
            arguments.pop("_tenant", None)
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
        self.ttl_map = {
            "get_current_time": 1,
            "query_database": 30,
            "list_events": 1,
        }

    def _get_cache_key(self, tool_name, arguments):
        return f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        if tool_name == "get_current_time":
            return await super().handle_task(task)

        cache_key = self._get_cache_key(tool_name, arguments)
        now = time.time()
        cached = self.cache.get(cache_key)
        if cached and cached[1] > now:
            print(f"[QueryWorker] 缓存命中: {cache_key}", flush=True)
            return {"result": cached[0]}

        result = await super().handle_task(task)
        if "result" in result and "error" not in result:
            ttl = self.ttl_map.get(tool_name, 10)
            self.cache[cache_key] = (result["result"], now + ttl)
            print(f"[QueryWorker] 缓存写入 (TTL={ttl}s): {cache_key}", flush=True)
        return result