# agents.py
import asyncio
import json
import traceback
import time
from functools import partial
from typing import Any, Dict, Callable

class Agent:
    def __init__(self, name: str, event_bus: 'RedisEventBus'):
        self.name = name
        self.bus = event_bus
        self.is_running = False
        self.task_count = 0
        self.error_count = 0

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """发送任务到该智能体，并等待结果返回"""
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        return await future

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_loop(self):
        """Worker 主循环：从 Redis 任务队列拉取任务，执行后放入结果队列"""
        task_queue = f"task:{self.name}"
        result_queue = f"result:{self.name}"
        print(f"[{self.name}] Redis Worker 启动，监听队列: {task_queue}", flush=True)
        self.is_running = True
        while True:
            try:
                # 阻塞式弹出任务，超时 5 秒
                result = await self.bus.redis.brpop(task_queue, timeout=5)
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
                # 将结果放入结果队列
                result_payload = {"task_id": task_id, "result": res}
                await self.bus.redis.lpush(result_queue, json.dumps(result_payload))
                print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count})")
            except Exception as e:
                print(f"[{self.name}] 循环异常: {type(e).__name__}: {e}", flush=True)
                await asyncio.sleep(1)

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
        # 移除内部使用的 _tenant 参数
        arguments.pop("_tenant", None)
        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}
        func = self.tools[tool_name]
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, partial(func, **arguments))
        return {"result": result}

class QueryWorker(WorkerAgent):
    """带缓存的查询 Worker，时间查询禁用缓存"""
    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, tools, event_bus)
        self.cache = {}
        self.ttl_map = {
            "get_current_time": 1,
            "query_database": 30,
            "list_events": 30,
        }

    def _get_cache_key(self, tool_name, arguments):
        return f"{tool_name}:{json.dumps(arguments, sort_keys=True)}"

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        # 时间查询：跳过缓存，直接执行
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