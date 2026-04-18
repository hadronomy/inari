from __future__ import annotations

import asyncio
import contextlib
import logging
import random

from ..config import AgentSettings
from ..security.certificate_lifecycle import ManagedCertificateLifecycleManager
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
        certificate_lifecycle_manager: ManagedCertificateLifecycleManager | None,
        runtime_event_forwarder: GatewayRuntimeEventForwarder,
    ) -> None:
        self.settings = settings
        self.connector = connector
        self.certificate_lifecycle_manager = certificate_lifecycle_manager
        self.runtime_event_forwarder = runtime_event_forwarder
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False

    async def start(self) -> None:
        if self._started or self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        self._tasks = [
            *(
                [
                    asyncio.create_task(
                        self.certificate_lifecycle_manager.run_forever(),
                        name="iot-agent-gateway-certificate-lifecycle",
                    )
                ]
                if self.certificate_lifecycle_manager is not None
                else []
            ),
            asyncio.create_task(self._sync_loop(), name="iot-agent-gateway-sync"),
            asyncio.create_task(
                self._data_plane_loop(), name="iot-agent-gateway-data-plane"
            ),
            asyncio.create_task(
                self._outbox_loop(), name="iot-agent-gateway-outbox"
            ),
            asyncio.create_task(
                self.runtime_event_forwarder.run_forever(),
                name="iot-agent-gateway-runtime-forwarder",
            ),
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
        await self.connector.close()
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

    async def _data_plane_loop(self) -> None:
        backoff = _Backoff(
            base_delay=self.settings.gateway_backoff_base_seconds,
            max_delay=self.settings.gateway_backoff_max_seconds,
        )
        while True:
            try:
                await self.connector.listen_once()
                delay = max(
                    self.settings.gateway_reconnect_delay_seconds,
                    self.settings.gateway_outbox_poll_interval_seconds,
                )
                backoff.reset()
                await asyncio.sleep(delay)
            except Exception:
                logger.exception("Gateway data-plane loop failed")
                delay = backoff.next_delay()
                await self.connector._update_status(retry_delay_seconds=delay)  # noqa: SLF001
                await asyncio.sleep(delay)

    async def _outbox_loop(self) -> None:
        backoff = _Backoff(
            base_delay=self.settings.gateway_backoff_base_seconds,
            max_delay=self.settings.gateway_backoff_max_seconds,
        )
        while True:
            try:
                await self.connector.flush_outbox_once()
                backoff.reset()
                await asyncio.sleep(self.settings.gateway_outbox_poll_interval_seconds)
            except Exception:
                logger.exception("Gateway outbox loop failed")
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
