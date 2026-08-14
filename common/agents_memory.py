# common/agents_memory.py
"""
内存总线下的多智能体 Worker 实现。

包括：
- Agent 基类：负责任务队列、统计和生命周期管理
- WorkerAgent：执行具体工具函数
- QueryWorker：带 TTL 缓存的查询型 Worker
"""

import asyncio
import json
import time
import traceback
from functools import partial
from typing import Any, Callable, Dict, Optional

class Agent:
    """基础智能体，维护任务队列与统计信息"""

    def __init__(self, name: str, event_bus: 'EventBus'):
        self.name = name
        self.bus = event_bus
        self.queue: asyncio.Queue = asyncio.Queue()
        self.is_running = False
        self.task_count = 0
        self.error_count = 0
        self.total_time = 0.0   # 总耗时（秒）

    async def send_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        向该智能体发送任务并等待结果。

        参数:
            task: 包含 tool 和 arguments 的字典

        返回:
            处理结果字典，格式为 {"result": ...} 或 {"error": ...}
        """
        future = asyncio.get_event_loop().create_future()
        self.queue.put_nowait((task, future))
        return await future

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """子类必须实现，处理具体任务"""
        raise NotImplementedError

    async def run_loop(self) -> None:
        """主循环：从队列中取出任务并执行，直到被取消"""
        print(f"[{self.name}] Worker 启动（内存总线），等待任务...")
        self.is_running = True
        try:
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
                finally:
                    elapsed = time.monotonic() - start_time
                    self.total_time += elapsed
                    if not future.done():
                        future.set_result(result)
                    print(f"[{self.name}] 任务完成 (成功: {self.task_count}, 失败: {self.error_count}, 耗时: {elapsed:.2f}s)")
        except asyncio.CancelledError:
            print(f"[{self.name}] Worker 循环被取消")
            self.is_running = False

    def get_stats(self) -> Dict[str, Any]:
        """返回运行统计信息"""
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
    """通用工具执行智能体"""

    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, event_bus)
        self.tools = tools
        # 需要保留 _tenant 参数的工具（用于租户隔离）
        self.tenant_aware_tools = {"add_event", "list_events", "delete_event"}

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        # 除非工具需要租户信息，否则移除内部参数
        if tool_name not in self.tenant_aware_tools:
            arguments.pop("_tenant", None)

        if tool_name not in self.tools:
            return {"error": f"工具 {tool_name} 不存在"}

        func = self.tools[tool_name]
        loop = asyncio.get_event_loop()
        # 在线程池中执行同步函数，避免阻塞事件循环
        result = await loop.run_in_executor(None, partial(func, **arguments))
        return {"result": result}


class QueryWorker(WorkerAgent):
    """带 TTL 缓存的查询智能体，对时间查询跳过缓存"""

    def __init__(self, name: str, tools: Dict[str, Callable], event_bus: 'EventBus'):
        super().__init__(name, tools, event_bus)
        self.cache: Dict[str, tuple] = {}  # key -> (result, expiry_time)
        self.ttl_map = {
            "get_current_time": 1,
            "query_database": 30,
            "list_events": 1,
        }

    def _get_cache_key(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """生成缓存键（工具名 + 排序后的参数）"""
        args_json = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        return f"{tool_name}:{args_json}"

    async def handle_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task.get("tool")
        arguments = task.get("arguments", {})

        # 时间查询不使用缓存
        if tool_name == "get_current_time":
            return await super().handle_task(task)

        cache_key = self._get_cache_key(tool_name, arguments)
        now = time.time()
        cached = self.cache.get(cache_key)
        if cached and cached[1] > now:
            print(f"[QueryWorker] 缓存命中: {cache_key}", flush=True)
            return {"result": cached[0]}

        result = await super().handle_task(task)

        # 仅缓存成功且有效的结果，并设置过期时间
        if "result" in result and "error" not in result:
            ttl = self.ttl_map.get(tool_name, 10)
            self.cache[cache_key] = (result["result"], now + ttl)
            print(f"[QueryWorker] 缓存写入 (TTL={ttl}s): {cache_key}", flush=True)

        return result