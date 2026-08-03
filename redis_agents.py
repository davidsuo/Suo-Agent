# redis_agents.py
import asyncio
import uuid
import json
import traceback
from functools import partial
from typing import Any, Dict, Callable
import redis.asyncio as redis

class RedisAgent:
    def __init__(self, name: str, redis_url: str):
        self.name = name
        self.queue_key = f"agent:{name}:queue"
        self.result_key_prefix = f"agent:{name}:result:"
        self.redis = redis.from_url(redis_url)
        self.is_running = False
        self.task_count = 0
        self.error_count = 0

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """发送任务到 Redis 队列，并等待结果返回"""
        task_id = str(uuid.uuid4())
        task["task_id"] = task_id
        result_key = self.result_key_prefix + task_id

        # 将任务放入队列（JSON 序列化）
        await self.redis.lpush(self.queue_key, json.dumps(task))
        print(f"[{self.name}] 任务已入队: {task_id}")

        # 轮询等待结果
        while True:
            result_data = await self.redis.get(result_key)
            if result_data:
                await self.redis.delete(result_key)  # 清理结果
                return json.loads(result_data)
            await asyncio.sleep(0.2)

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    async def run_loop(self):
        """Worker 主循环：从 Redis 队列中取出任务并处理"""
        print(f"[{self.name}] Redis Worker 启动，队列: {self.queue_key}")
        self.is_running = True
        while True:
            # 阻塞式弹出任务（BRPOP）
            result = await self.redis.brpop(self.queue_key, timeout=5)
            if result is None:
                continue  # 超时无任务，继续等待
            _, task_data = result
            task = json.loads(task_data)
            task_id = task.get("task_id")
            print(f"[{self.name}] 收到任务: {task.get('tool', 'unknown')} (ID: {task_id})")
            try:
                res = await self.handle_task(task)
                self.task_count += 1
            except Exception as e:
                res = {"error": str(e), "traceback": traceback.format_exc()}
                self.error_count += 1
            # 将结果存入 Redis
            result_key = self.result_key_prefix + task_id
            await self.redis.set(result_key, json.dumps(res), ex=300)  # 5分钟过期
            print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count})")

class SearchWorkerRedis(RedisAgent):
    def __init__(self, tools: Dict[str, Callable], redis_url: str):
        super().__init__("SearchWorker", redis_url)
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

class CodeWorkerRedis(RedisAgent):
    def __init__(self, tools: Dict[str, Callable], redis_url: str):
        super().__init__("CodeWorker", redis_url)
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

class DataWorkerRedis(RedisAgent):
    def __init__(self, tools: Dict[str, Callable], redis_url: str):
        super().__init__("DataWorker", redis_url)
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