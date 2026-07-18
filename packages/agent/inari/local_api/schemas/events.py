from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from .base import APIModel
from ...runtime.models import RuntimeEvent


class RuntimeResourceKind(StrEnum):
    DEVICE = "device"
    JOB = "job"


class RuntimeEventKind(StrEnum):
    DEVICE_CONNECTED = "device.connected"
    DEVICE_DISCONNECTED = "device.disconnected"
    DEVICE_UPDATED = "device.updated"
    JOB_CANCELLED = "job.cancelled"
    JOB_FAILED = "job.failed"
    JOB_QUEUED = "job.queued"
    JOB_RETRY_SCHEDULED = "job.retry_scheduled"
    JOB_SUCCEEDED = "job.succeeded"


class RuntimeEventResponse(APIModel):
    sequence: int
    resource_kind: RuntimeResourceKind
    resource_id: str
    event_type: RuntimeEventKind
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, event: RuntimeEvent) -> RuntimeEventResponse:
        return cls(
            sequence=event.sequence,
            resource_kind=RuntimeResourceKind(event.resource_kind),
            resource_id=event.resource_id,
            event_type=RuntimeEventKind(event.event_type),
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        )


class DeviceEventCollectionResponse(APIModel):
    ok: Literal[True] = True
    events: list[RuntimeEventResponse]
