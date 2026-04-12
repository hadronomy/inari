from __future__ import annotations

import asyncio
import contextlib
import logging

from ..config import AgentSettings
from ..security.models import GatewayMode
from .connector import GatewayConnector

logger = logging.getLogger(__name__)


class GatewaySupervisor:
    def __init__(self, *, settings: AgentSettings, connector: GatewayConnector) -> None:
        self.settings = settings
        self.connector = connector
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    async def start(self) -> None:
        if self._started or self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        self._tasks = [
            asyncio.create_task(self._sync_loop(), name="iot-agent-gateway-sync"),
            asyncio.create_task(self._events_loop(), name="iot-agent-gateway-events"),
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
        while True:
            try:
                await self.connector.sync_once()
            except Exception:
                logger.exception("Gateway sync loop failed")
            await asyncio.sleep(self.settings.gateway_sync_interval_seconds)

    async def _events_loop(self) -> None:
        while True:
            try:
                await self.connector.listen_once()
            except Exception:
                logger.exception("Gateway event stream loop failed")
            await asyncio.sleep(self.settings.gateway_reconnect_delay_seconds)
