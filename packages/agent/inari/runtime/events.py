from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from .models import RuntimeEvent


@dataclass(slots=True)
class EventSubscription:
    queue: asyncio.Queue[RuntimeEvent]


class EventHub:
    def __init__(self, *, queue_size: int = 256) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[RuntimeEvent]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: RuntimeEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            if subscriber.full():
                try:
                    subscriber.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                continue

    async def subscribe(self) -> EventSubscription:
        queue: asyncio.Queue[RuntimeEvent] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return EventSubscription(queue=queue)

    async def unsubscribe(self, subscription: EventSubscription) -> None:
        async with self._lock:
            self._subscribers.discard(subscription.queue)

    async def iter_events(self) -> AsyncIterator[RuntimeEvent]:
        subscription = await self.subscribe()
        try:
            while True:
                yield await subscription.queue.get()
        finally:
            await self.unsubscribe(subscription)
