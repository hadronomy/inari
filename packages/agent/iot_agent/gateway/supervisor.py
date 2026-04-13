from __future__ import annotations

import asyncio
import contextlib
import logging
import random

from ..config import AgentSettings
from ..security.models import GatewayMode
from .connector import GatewayConnector
from .runtime_bridge import GatewayRuntimeEventForwarder

logger = logging.getLogger(__name__)


class GatewaySupervisor:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        connector: GatewayConnector,
        runtime_event_forwarder: GatewayRuntimeEventForwarder,
    ) -> None:
        self.settings = settings
        self.connector = connector
        self.runtime_event_forwarder = runtime_event_forwarder
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    async def start(self) -> None:
        if self._started or self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        self._tasks = [
            asyncio.create_task(self._sync_loop(), name="iot-agent-gateway-sync"),
            asyncio.create_task(self._events_loop(), name="iot-agent-gateway-events"),
            asyncio.create_task(self.runtime_event_forwarder.run_forever(), name="iot-agent-gateway-runtime-forwarder"),
        ]
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._started = False

    async def _sync_loop(self) -> None:
        backoff = _Backoff(
            base_delay=self.settings.gateway_backoff_base_seconds,
            max_delay=self.settings.gateway_backoff_max_seconds,
        )
        while True:
            try:
                await self.connector.sync_once()
                backoff.reset()
                await asyncio.sleep(self.settings.gateway_sync_interval_seconds)
            except Exception:
                logger.exception("Gateway sync loop failed")
                delay = backoff.next_delay()
                await self.connector._update_status(retry_delay_seconds=delay)  # noqa: SLF001
                await asyncio.sleep(delay)

    async def _events_loop(self) -> None:
        backoff = _Backoff(
            base_delay=self.settings.gateway_backoff_base_seconds,
            max_delay=self.settings.gateway_backoff_max_seconds,
        )
        while True:
            try:
                await self.connector.listen_once()
                delay = max(
                    self.settings.gateway_reconnect_delay_seconds,
                    self.settings.gateway_control_poll_interval_seconds,
                )
                backoff.reset()
                await asyncio.sleep(delay)
            except Exception:
                logger.exception("Gateway event stream loop failed")
                delay = backoff.next_delay()
                await self.connector._update_status(retry_delay_seconds=delay)  # noqa: SLF001
                await asyncio.sleep(delay)


class _Backoff:
    def __init__(self, *, base_delay: float, max_delay: float) -> None:
        self.base_delay = max(0.1, base_delay)
        self.max_delay = max(self.base_delay, max_delay)
        self.failures = 0

    def reset(self) -> None:
        self.failures = 0

    def next_delay(self) -> float:
        self.failures += 1
        raw_delay = min(self.max_delay, self.base_delay * (2 ** (self.failures - 1)))
        jitter = raw_delay * 0.2 * random.random()
        return raw_delay + jitter
