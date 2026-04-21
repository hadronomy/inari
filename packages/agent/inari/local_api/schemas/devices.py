from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from .base import APIModel
from ...drivers import DeviceKind, DriverMetadata
from ...printing.protocols import PrinterTransport
from ...runtime.models import (
    DeviceClass,
    DeviceConnectionState,
    DeviceRecord,
)


class DefaultDeviceSummaryResponse(APIModel):
    id: str
    name: str


class DeviceDirectorySummaryResponse(APIModel):
    count: int
    online_count: int
    offline_count: int
    kind_counts: dict[str, int]
    default_device: DefaultDeviceSummaryResponse | None = None

    @classmethod
    def from_devices(
        cls, devices: list[DeviceRecord]
    ) -> DeviceDirectorySummaryResponse:
        kind_counts: dict[str, int] = {}
        for device in devices:
            kind_counts[device.kind.value] = kind_counts.get(device.kind.value, 0) + 1
        default_device = next((device for device in devices if device.is_default), None)
        return cls(
            count=len(devices),
            online_count=sum(
                1
                for device in devices
                if device.connection_state is DeviceConnectionState.ONLINE
            ),
            offline_count=sum(
                1
                for device in devices
                if device.connection_state is DeviceConnectionState.OFFLINE
            ),
            kind_counts=kind_counts,
            default_device=(
                DefaultDeviceSummaryResponse(
                    id=default_device.id, name=default_device.name
                )
                if default_device is not None
                else None
            ),
        )


class PrinterCapability(StrEnum):
    CASH_DRAWER = "cash_drawer"


class DeviceConnectionResponse(APIModel):
    state: DeviceConnectionState
    first_seen_at: datetime
    last_seen_at: datetime
    observed_at: datetime


class PrinterDetailsResponse(APIModel):
    is_default: bool
    preferred_transport: PrinterTransport | None = None
    supported_transports: tuple[PrinterTransport, ...] = ()
    capabilities: tuple[PrinterCapability, ...] = ()


class DriverResponse(APIModel):
    key: str
    display_name: str
    kind: DeviceKind
    platform: str

    @classmethod
    def from_metadata(cls, metadata: DriverMetadata) -> DriverResponse:
        return cls(
            key=metadata.key,
            display_name=metadata.display_name,
            kind=metadata.kind,
            platform=metadata.platform,
        )


class DeviceResponse(APIModel):
    id: str
    kind: DeviceKind
    device_class: DeviceClass
    name: str
    driver_key: str
    driver: DriverResponse | None = None
    connection: DeviceConnectionResponse
    printer: PrinterDetailsResponse | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(
        cls,
        device: DeviceRecord,
        *,
        driver_metadata: DriverMetadata | None = None,
    ) -> DeviceResponse:
        printer_details: PrinterDetailsResponse | None = None
        if device.kind is DeviceKind.PRINTER:
            printer_details = PrinterDetailsResponse(
                is_default=device.is_default,
                preferred_transport=device.preferred_transport,
                supported_transports=device.supported_transports,
                capabilities=tuple(
                    PrinterCapability(value) for value in device.capability_keys
                ),
            )
        return cls(
            id=device.id,
            kind=device.kind,
            device_class=device.device_class,
            name=device.name,
            driver_key=device.driver_key,
            driver=(
                DriverResponse.from_metadata(driver_metadata)
                if driver_metadata is not None
                else None
            ),
            connection=DeviceConnectionResponse(
                state=device.connection_state,
                first_seen_at=device.first_seen_at,
                last_seen_at=device.last_seen_at,
                observed_at=device.observed_at,
            ),
            printer=printer_details,
            metadata=dict(device.metadata),
        )


class DeviceDirectoryResponse(APIModel):
    devices: list[DeviceResponse]
    summary: DeviceDirectorySummaryResponse


class DeviceResourceResponse(APIModel):
    device: DeviceResponse
