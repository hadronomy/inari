from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import APIModel
from ...runtime.models import RuntimeEvent, RuntimeEventKind, RuntimeResourceKind


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
            resource_kind=event.resource_kind,
            resource_id=event.resource_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
        )


class DeviceEventCollectionResponse(APIModel):
    ok: Literal[True] = True
    events: list[RuntimeEventResponse]
