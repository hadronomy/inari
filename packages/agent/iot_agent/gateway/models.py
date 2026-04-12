from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..security.models import GatewayMode


class UpstreamConnectionState(StrEnum):
    DISABLED = "disabled"
    DISCONNECTED = "disconnected"
    ENROLLING = "enrolling"
    CONNECTING = "connecting"
    ONLINE = "online"
    DEGRADED = "degraded"


@dataclass(slots=True, frozen=True)
class GatewayEnrollmentRecord:
    access_token: str
    enrolled_at: datetime
    expires_at: datetime | None = None
    status_url: str | None = None
    events_url: str | None = None


@dataclass(slots=True, frozen=True)
class UpstreamStatus:
    mode: GatewayMode
    state: UpstreamConnectionState
    base_url: str | None = None
    status_url: str | None = None
    events_url: str | None = None
    enrolled_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_event_at: datetime | None = None
    detail: str | None = None
    last_error: str | None = None
