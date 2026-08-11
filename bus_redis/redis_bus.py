# bus_redis/redis_bus.py
import asyncio
import json
import uuid
import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError

class RedisEventBus:
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(
            redis_url,
            decode_responses=True,
            ssl_cert_reqs=None,
            socket_keepalive=True,
            socket_connect_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30
        )
        self._futures = {}

    async def publish(self, event_type: str, data: dict) -> None:
        worker_name = event_type.split(".")[-1]
        task_queue = f"task:{worker_name}"
        task = data["task"]
        future = data["future"]

        task_id = str(uuid.uuid4())
        task["task_id"] = task_id
        self._futures[task_id] = future

        await self.redis.lpush(task_queue, json.dumps(task))
        asyncio.create_task(self._wait_for_result(task_id, worker_name))

    async def _wait_for_result(self, task_id: str, worker_name: str):
        result_queue = f"result:{worker_name}"
        timeout = 60
        start = asyncio.get_event_loop().time()
        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                future = self._futures.pop(task_id, None)
                if future and not future.done():
                    future.set_result({"error": "任务超时"})
                break
            try:
                result = await self.redis.brpop(result_queue, timeout=5)
                if result is not None:
                    _, data = result
                    res_data = json.loads(data)
                    if res_data.get("task_id") == task_id:
                        future = self._futures.pop(task_id, None)
                        if future and not future.done():
                            future.set_result(res_data.get("result", {}))
                        break
            except (ConnectionError, TimeoutError):
                continue
            except Exception as e:
                print(f"[RedisBus] 等待结果异常: {e}", flush=True)
                await asyncio.sleep(1)