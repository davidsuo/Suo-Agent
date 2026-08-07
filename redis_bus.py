# redis_bus.py
import asyncio
import json
import uuid
import traceback
import redis.asyncio as redis

class RedisEventBus:
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(
            redis_url,
            decode_responses=True,
            ssl_cert_reqs=None,       # Upstash 免费层建议
            socket_keepalive=True,
            socket_connect_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30
        )
        # 存储当前进程内的 pending futures，键为 task_id
        self._futures = {}

    async def publish(self, event_type: str, data: dict) -> dict:
        """
        发布任务到指定 Worker 的队列，并等待结果返回。
        event_type 格式：ToolRequested.WorkerName
        data 必须包含 'task' 和 'future'
        """
        worker_name = event_type.split(".")[-1]
        task_queue = f"task:{worker_name}"
        task = data["task"]
        future = data["future"]

        # 生成唯一任务ID
        task_id = str(uuid.uuid4())
        task["task_id"] = task_id

        # 将 future 保存在本进程内
        self._futures[task_id] = future

        # 将任务推入 Worker 的任务队列
        await self.redis.lpush(task_queue, json.dumps(task))

        # 启动一个后台任务等待结果（不阻塞 publish）
        asyncio.create_task(self._wait_for_result(task_id, worker_name))

    async def _wait_for_result(self, task_id: str, worker_name: str):
        """
        轮询结果队列，直到获取对应 task_id 的结果或超时。
        """
        result_queue = f"result:{worker_name}"
        timeout = 60  # 秒
        start = asyncio.get_event_loop().time()
        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > timeout:
                future = self._futures.pop(task_id, None)
                if future and not future.done():
                    future.set_result({"error": "任务超时"})
                break
            try:
                # 使用 BRPOP 阻塞等待，但设置 1 秒超时以便检查总超时
                result = await self.redis.brpop(result_queue, timeout=1)
                if result is not None:
                    _, data = result
                    res_data = json.loads(data)
                    if res_data.get("task_id") == task_id:
                        future = self._futures.pop(task_id, None)
                        if future and not future.done():
                            future.set_result(res_data.get("result", {}))
                        break
            except Exception as e:
                # 连接异常等，记录日志并继续尝试
                print(f"[RedisBus] 等待结果异常: {e}", flush=True)
                await asyncio.sleep(1)

    async def subscribe(self, event_type: str, callback):
        """在 Redis 总线中，Worker 不通过回调，而是主动拉取，此方法保留接口但不实现。"""
        pass