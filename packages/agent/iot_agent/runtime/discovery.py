from __future__ import annotations

import logging
from collections.abc import Iterable

from ..drivers import DeviceKind, DriverRegistry
from ..exceptions import AgentError
from .events import EventHub
from .models import DeviceConnectionState, DeviceRecord, utc_now
from .repositories import DeviceRepository

logger = logging.getLogger(__name__)


class DiscoveryCoordinator:
    def __init__(
        self,
        *,
        driver_registry: DriverRegistry,
        device_repository: DeviceRepository,
        event_hub: EventHub,
    ) -> None:
        self.driver_registry = driver_registry
        self.device_repository = device_repository
        self.event_hub = event_hub

    async def sync_once(self) -> tuple[DeviceRecord, ...]:
        observed_at = utc_now()
        previous = {device.id: device for device in self.device_repository.list()}
        current = {device.id: device for device in self._discover_devices(observed_at=observed_at)}

        for device in current.values():
            prior = previous.get(device.id)
            saved = self.device_repository.upsert(device)
            if prior is None:
                await self._publish_device_event("device.connected", saved)
                continue
            if prior.connection_state is DeviceConnectionState.OFFLINE:
                await self._publish_device_event("device.connected", saved)
                continue
            if prior != saved:
                await self._publish_device_event("device.updated", saved)

        for device_id, prior in previous.items():
            if device_id in current or prior.connection_state is DeviceConnectionState.OFFLINE:
                continue
            offline = prior.with_connection_state(DeviceConnectionState.OFFLINE, observed_at=observed_at)
            saved = self.device_repository.upsert(offline)
            await self._publish_device_event("device.disconnected", saved)

        return tuple(self.device_repository.list())

    def _discover_devices(self, *, observed_at) -> Iterable[DeviceRecord]:
        yield from self._discover_printers(observed_at=observed_at)

    def _discover_printers(self, *, observed_at) -> Iterable[DeviceRecord]:
        for driver in self.driver_registry.printer_drivers(available_only=False):
            if not driver.is_available():
                continue
            try:
                printers = tuple(driver.list_devices())
            except Exception as exc:  # pragma: no cover - defensive runtime path
                logger.warning("Printer discovery failed for %s", driver.metadata.key, exc_info=True)
                raise AgentError(
                    "DEVICE_DISCOVERY_FAILED",
                    f"Printer discovery failed for driver {driver.metadata.key!r}.",
                    status_code=503,
                    details={"driver": driver.metadata.key, "cause": type(exc).__name__},
                ) from exc
            for printer in printers:
                yield DeviceRecord.from_printer(printer, observed_at=observed_at)

    async def _publish_device_event(self, event_type: str, device: DeviceRecord) -> None:
        event = self.device_repository.append_event(
            device_id=device.id,
            event_type=event_type,
            payload=_device_event_payload(device),
        )
        await self.event_hub.publish(event)


def _device_event_payload(device: DeviceRecord) -> dict[str, object]:
    return {
        "device_id": device.id,
        "kind": device.kind.value,
        "name": device.name,
        "driver": device.driver_key,
        "connection_state": device.connection_state.value,
        "is_default": device.is_default,
        "preferred_transport": device.preferred_transport.value if device.preferred_transport is not None else None,
        "capabilities": dict(device.capabilities),
    }
