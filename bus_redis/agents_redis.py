# bus_redis/agents_redis.py
import asyncio
import json
import traceback
import time
from functools import partial
from typing import Any, Dict, Callable
from redis.exceptions import ConnectionError, TimeoutError

class Agent:
    def __init__(self, name: str, event_bus: 'RedisEventBus'):
        self.name = name
        self.bus = event_bus
        self.is_running = False
        self.task_count = 0
        self.error_count = 0

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """通过 Redis 总线发送任务并等待结果"""
        future = asyncio.get_event_loop().create_future()
        event_data = {"task": task, "future": future}
        await self.bus.publish(f"ToolRequested.{self.name}", event_data)
        return await future

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_loop(self):
        """Worker 主循环：从 Redis 任务队列拉取任务并执行"""
        task_queue = f"task:{self.name}"
        result_queue = f"result:{self.name}"
        print(f"[{self.name}] Redis Worker 启动，监听队列: {task_queue}", flush=True)
        self.is_running = True
        while True:
            try:
                result = await self.bus.redis.brpop(task_queue, timeout=10)
                if result is None:
                    continue
                _, task_data = result
                task = json.loads(task_data)
                task_id = task.get("task_id")
                print(f"[{self.name}] 收到任务: {task.get('tool', 'unknown')} (ID: {task_id})")
                try:
                    res = await self.handle_task(task)
                    self.task_count += 1
                except Exception as e:
                    self.error_count += 1
                    res = {"error": str(e), "traceback": traceback.format_exc()}
                result_payload = {"task_id": task_id, "result": res}
                await self.bus.redis.lpush(result_queue, json.dumps(result_payload))
                print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count})")
            except (ConnectionError, TimeoutError):
                await asyncio.sleep(1)
            except Exception as e:
                print(f"[{self.name}] 循环异常: {e}", flush=True)
                await asyncio.sleep(5)

    def get_stats(self):
        return {
            "name": self.name,
            "is_running": self.is_running,
            "task_count": self.task_count,
            "error_count": self.error_count,
            "queue_size": "N/A"
        }

class WorkerAgent(Agent):
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'RedisEventBus'):
        super().__init__(name, event_bus)
        self.tools = tools

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})
        if tool_name not in ("add_event", "list_events", "delete_event"):
            arguments.pop("_tenant", None)
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        func = self.tools[tool_name]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(func, **arguments))
        return {"result": result}

class QueryWorker(WorkerAgent):
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'RedisEventBus'):
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