from __future__ import annotations

from ..jobs.operations import DeviceTargetRef
from ..models import DeviceRecord, RuntimeEvent
from ..repositories import DeviceRepository
from .discovery import DiscoveryCoordinator
from ...core.exceptions import AgentError
from ...drivers import DeviceKind, DriverMetadata
from ...printing.service import PrinterService


class DeviceCatalog:
    def __init__(
        self,
        *,
        device_repository: DeviceRepository,
        discovery: DiscoveryCoordinator,
        printer_service: PrinterService,
    ) -> None:
        self.device_repository = device_repository
        self.discovery = discovery
        self.printer_service = printer_service

    def list_devices(self) -> tuple[DeviceRecord, ...]:
        return self.device_repository.list()

    def get_device(self, device_id: str) -> DeviceRecord | None:
        return self.device_repository.get(device_id)

    def get_driver_metadata(
        self, *, kind: DeviceKind, driver_key: str
    ) -> DriverMetadata | None:
        for driver in self.discovery.driver_registry.drivers_for(
            kind, available_only=False
        ):
            if driver.metadata.key == driver_key:
                return driver.metadata
        return None

    def list_device_events(
        self, device_id: str, *, limit: int = 50
    ) -> tuple[RuntimeEvent, ...]:
        return self.device_repository.list_events(device_id, limit=limit)

    async def refresh(self) -> tuple[DeviceRecord, ...]:
        return await self.discovery.sync_once()

    async def resolve_target(self, target: DeviceTargetRef) -> DeviceRecord:
        if target.device_id:
            device = self.device_repository.get(target.device_id)
            if device is None:
                await self.refresh()
                device = self.device_repository.get(target.device_id)
            if device is None:
                raise AgentError(
                    "DEVICE_NOT_FOUND",
                    f"Device {target.device_id!r} was not found.",
                    status_code=404,
                )
            return device

        selected = self.printer_service.resolve_printer(target.printer_name)
        selected_record = DeviceRecord.from_printer(selected)
        device = self.device_repository.get(selected_record.id)
        if device is not None:
            return device

        await self.refresh()
        device = self.device_repository.get(selected_record.id)
        if device is None:
            raise AgentError(
                "DEVICE_NOT_FOUND",
                f"Printer {selected.name!r} was not found in the runtime device catalog.",
                status_code=404,
            )
        return device
