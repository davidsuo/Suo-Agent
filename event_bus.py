# event_bus.py
import asyncio
from typing import Any, Dict, Callable, Coroutine

class EventBus:
    def __init__(self):
        self.subscribers: Dict[str, list[Callable[..., Coroutine]]] = {}

    def subscribe(self, event_type: str, callback: Callable[..., Coroutine]):
        """订阅事件，当事件发生时调用 callback(event_data)"""
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)

    async def publish(self, event_type: str, data: Dict[str, Any]):
        """发布事件，通知所有订阅者"""
        if event_type in self.subscribers:
            for callback in self.subscribers[event_type]:
                await callback(data)

# 全局单例
bus = EventBus()