from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .base import APIModel
from .devices import DeviceDirectorySummaryResponse
from .events import RuntimeEventResponse
from .jobs import QueueSummaryResponse
from ...printing.commands import DeviceCommandKind
from ...printing.jobs import PrintContentKind


class ServiceDescriptorResponse(APIModel):
    name: str
    version: str


class SystemStatusResponse(APIModel):
    ok: Literal[True] = True
    status: Literal["healthy"] = "healthy"
    service: ServiceDescriptorResponse
    devices: DeviceDirectorySummaryResponse
    queue: QueueSummaryResponse
    supported_content_kinds: tuple[PrintContentKind, ...]
    supported_device_commands: tuple[DeviceCommandKind, ...]


class LiveSnapshotResponse(APIModel):
    kind: Literal["snapshot"] = "snapshot"
    status: SystemStatusResponse


class LiveEventUpdateResponse(APIModel):
    kind: Literal["event_update"] = "event_update"
    status: SystemStatusResponse
    event: RuntimeEventResponse


LiveUpdateMessage = Annotated[
    LiveSnapshotResponse | LiveEventUpdateResponse,
    Field(discriminator="kind"),
]
