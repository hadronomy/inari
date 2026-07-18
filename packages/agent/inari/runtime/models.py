from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID, uuid5

from ..drivers import DeviceIdentity, DeviceKind
from ..printing.protocols import PrinterCapabilities, PrinterDevice, PrinterTransport


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def normalize_timestamp(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def timestamp_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_timestamp(value)
    assert normalized is not None
    return normalized.isoformat()


class DeviceConnectionState(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"


class DeviceClass(StrEnum):
    PHYSICAL = "physical"
    VIRTUAL = "virtual"


class JobKind(StrEnum):
    PRINT = "print_job"
    COMMAND = "device_command"


class JobState(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RuntimeResourceKind(StrEnum):
    DEVICE = "device"
    JOB = "job"


class RuntimeEventKind(StrEnum):
    DEVICE_CONNECTED = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"
    DEVICE_UPDATED = "device.updated"
    JOB_CANCELLED = "job.cancelled"
    JOB_DISPATCHED = "job.dispatched"
    JOB_FAILED = "job.failed"
    JOB_QUEUED = "job.queued"
    JOB_RECOVERED = "job.recovered"
    JOB_RETRY_SCHEDULED = "job.retry_scheduled"
    JOB_RUNNING = "job.running"
    JOB_SUCCEEDED = "job.succeeded"


INARI_DEVICE_NAMESPACE = UUID("efdbfb52-14ac-5c5c-a01c-b2a846f71d76")


def build_device_id(
    *, kind: DeviceKind, driver_key: str, identity: DeviceIdentity
) -> str:
    identity_key = f"{kind.value}\0{driver_key}\0{identity.stable_key()}"
    return f"dev_{uuid5(INARI_DEVICE_NAMESPACE, identity_key).hex}"


VIRTUAL_PRINTER_NAMES = {
    "fax",
    "microsoft print to pdf",
    "microsoft xps document writer",
}
VIRTUAL_PRINTER_NAME_HINTS = (
    "onenote",
    "virtual printer",
    "impresora virtual",
)


def infer_device_class(*, kind: DeviceKind, name: str) -> DeviceClass:
    normalized_name = name.casefold().strip()
    if kind is DeviceKind.PRINTER and (
        normalized_name in VIRTUAL_PRINTER_NAMES
        or any(hint in normalized_name for hint in VIRTUAL_PRINTER_NAME_HINTS)
    ):
        return DeviceClass.VIRTUAL
    return DeviceClass.PHYSICAL


@dataclass(slots=True, frozen=True)
class DeviceRecord:
    id: str
    kind: DeviceKind
    driver_key: str
    identity: DeviceIdentity
    name: str
    connection_state: DeviceConnectionState
    first_seen_at: datetime
    last_seen_at: datetime
    updated_at: datetime
    is_default: bool = False
    preferred_transport: PrinterTransport | None = None
    capabilities: Mapping[str, bool] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_printer(
        cls,
        printer: PrinterDevice,
        *,
        connection_state: DeviceConnectionState = DeviceConnectionState.ONLINE,
        observed_at: datetime | None = None,
    ) -> DeviceRecord:
        observed = normalize_timestamp(observed_at) or utc_now()
        return cls(
            id=build_device_id(
                kind=DeviceKind.PRINTER,
                driver_key=printer.driver_key,
                identity=printer.identity,
            ),
            kind=DeviceKind.PRINTER,
            driver_key=printer.driver_key,
            identity=printer.identity,
            name=printer.name,
            connection_state=connection_state,
            first_seen_at=observed,
            last_seen_at=observed,
            updated_at=observed,
            is_default=printer.is_default,
            preferred_transport=printer.preferred_transport,
            capabilities={
                "raw": printer.supports_raw,
                "text": printer.supports_text,
                "documents": printer.supports_documents,
                "cash_drawer": printer.supports_cash_drawer,
            },
            metadata={
                **dict(printer.metadata),
                "device_class": infer_device_class(
                    kind=DeviceKind.PRINTER, name=printer.name
                ).value,
            },
        )

    def to_printer_device(self) -> PrinterDevice:
        if self.kind is not DeviceKind.PRINTER:
            raise ValueError(f"Device {self.id!r} is not a printer.")
        return PrinterDevice(
            name=self.name,
            driver_key=self.driver_key,
            identity=self.identity,
            is_default=self.is_default,
            preferred_transport=self.preferred_transport or PrinterTransport.AUTO,
            capabilities=PrinterCapabilities(
                raw=bool(self.capabilities.get("raw", False)),
                text=bool(self.capabilities.get("text", False)),
                documents=bool(self.capabilities.get("documents", False)),
                cash_drawer=bool(self.capabilities.get("cash_drawer", False)),
            ),
        )

    @property
    def observed_at(self) -> datetime:
        return self.updated_at

    @property
    def device_class(self) -> DeviceClass:
        raw_device_class = self.metadata.get("device_class")
        if isinstance(raw_device_class, str):
            try:
                return DeviceClass(raw_device_class)
            except ValueError:
                pass
        return infer_device_class(kind=self.kind, name=self.name)

    @property
    def supported_transports(self) -> tuple[PrinterTransport, ...]:
        if self.kind is not DeviceKind.PRINTER:
            return ()

        supported: list[PrinterTransport] = []
        transport_flags = {
            PrinterTransport.RAW: bool(self.capabilities.get("raw", False)),
            PrinterTransport.TEXT: bool(self.capabilities.get("text", False)),
            PrinterTransport.DOCUMENT: bool(self.capabilities.get("documents", False)),
        }
        preferred = self.preferred_transport
        if (
            preferred is not None
            and preferred is not PrinterTransport.AUTO
            and transport_flags.get(preferred, False)
        ):
            supported.append(preferred)
        for transport in (
            PrinterTransport.RAW,
            PrinterTransport.TEXT,
            PrinterTransport.DOCUMENT,
        ):
            if transport_flags[transport] and transport not in supported:
                supported.append(transport)
        return tuple(supported)

    @property
    def capability_keys(self) -> tuple[str, ...]:
        if self.kind is not DeviceKind.PRINTER:
            return ()

        capability_names: list[str] = []
        if self.capabilities.get("cash_drawer", False):
            capability_names.append("cash_drawer")
        return tuple(capability_names)

    def with_connection_state(
        self,
        connection_state: DeviceConnectionState,
        *,
        observed_at: datetime | None = None,
    ) -> DeviceRecord:
        observed = normalize_timestamp(observed_at) or utc_now()
        return DeviceRecord(
            id=self.id,
            kind=self.kind,
            driver_key=self.driver_key,
            identity=self.identity,
            name=self.name,
            connection_state=connection_state,
            first_seen_at=self.first_seen_at,
            last_seen_at=observed
            if connection_state is DeviceConnectionState.ONLINE
            else self.last_seen_at,
            updated_at=observed,
            is_default=self.is_default,
            preferred_transport=self.preferred_transport,
            capabilities=dict(self.capabilities),
            metadata=dict(self.metadata),
        )


@dataclass(slots=True, frozen=True)
class RuntimeEvent:
    sequence: int
    resource_kind: RuntimeResourceKind
    resource_id: str
    event_type: RuntimeEventKind
    occurred_at: datetime
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class DeviceEventRecord(RuntimeEvent):
    resource_kind: RuntimeResourceKind = field(
        default=RuntimeResourceKind.DEVICE, init=False
    )


@dataclass(slots=True, frozen=True)
class JobEventRecord(RuntimeEvent):
    resource_kind: RuntimeResourceKind = field(
        default=RuntimeResourceKind.JOB, init=False
    )


@dataclass(slots=True, frozen=True)
class JobRecord:
    id: str
    kind: JobKind
    operation: str
    device_id: str
    device_kind: DeviceKind
    device_name: str
    state: JobState
    request_payload: Mapping[str, Any]
    request_metadata: Mapping[str, Any]
    content_kind: str | None
    command_kind: str | None
    attempt_count: int
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    next_run_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    lease_expires_at: datetime | None = None
    result_payload: Mapping[str, Any] | None = None
    last_error_code: str | None = None
    last_error_detail: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


@dataclass(slots=True, frozen=True)
class JobAttemptRecord:
    id: int
    job_id: str
    attempt_number: int
    state: JobState
    started_at: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    error_detail: str | None = None
    result_payload: Mapping[str, Any] | None = None
