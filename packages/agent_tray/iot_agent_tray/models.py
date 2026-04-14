from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from iot_agent.models import RuntimeEventResponse, SystemStatusResponse


class ControlMode(StrEnum):
    MONITOR = "monitor"
    SPAWN = "spawn"
    SERVICE = "service"


class LifecycleState(StrEnum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    STOPPING = "stopping"
    UNKNOWN = "unknown"


class TrayStatusLevel(StrEnum):
    ONLINE = "online"
    BUSY = "busy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    STARTING = "starting"
    STOPPED = "stopped"


WINDOWS_TRAY_TOOLTIP_MAX_LENGTH = 128


@dataclass(slots=True, frozen=True)
class TrayLinks:
    api_base_url: str
    docs_url: str
    devices_url: str
    jobs_url: str
    log_dir: Path


@dataclass(slots=True, frozen=True)
class ControlSnapshot:
    mode: ControlMode
    lifecycle: LifecycleState = LifecycleState.UNKNOWN
    detail: str | None = None
    can_start: bool = False
    can_stop: bool = False
    can_restart: bool = False
    managed_by_tray: bool = False

    @property
    def label(self) -> str:
        mode_label = {
            ControlMode.MONITOR: "Monitor",
            ControlMode.SPAWN: "Local Process",
            ControlMode.SERVICE: "Service",
        }[self.mode]
        lifecycle_label = {
            LifecycleState.RUNNING: "Running",
            LifecycleState.STOPPED: "Stopped",
            LifecycleState.STARTING: "Starting",
            LifecycleState.STOPPING: "Stopping",
            LifecycleState.UNKNOWN: "Unknown",
        }[self.lifecycle]
        return f"{mode_label}: {lifecycle_label}"


@dataclass(slots=True, frozen=True)
class TraySnapshot:
    title: str
    links: TrayLinks
    control: ControlSnapshot
    level: TrayStatusLevel
    connected: bool
    service_name: str | None = None
    service_version: str | None = None
    device_count: int = 0
    online_devices: int = 0
    offline_devices: int = 0
    queue_total: int = 0
    queue_dispatched: int = 0
    queue_running: int = 0
    queue_retry_scheduled: int = 0
    queue_failed: int = 0
    last_error: str | None = None
    last_event_type: str | None = None
    last_event_detail: str | None = None
    updated_at: datetime | None = None

    @classmethod
    def initial(cls, *, title: str, links: TrayLinks, control: ControlSnapshot) -> TraySnapshot:
        return cls(
            title=title,
            links=links,
            control=control,
            level=_level_for_offline_state(control),
            connected=False,
            updated_at=utc_now(),
        )

    @classmethod
    def from_status(
        cls,
        *,
        title: str,
        links: TrayLinks,
        control: ControlSnapshot,
        status: SystemStatusResponse,
        previous: TraySnapshot | None = None,
    ) -> TraySnapshot:
        queue = status.queue
        devices = status.devices
        snapshot = cls(
            title=title,
            links=links,
            control=control,
            level=_level_for_status(status),
            connected=True,
            service_name=status.service.name,
            service_version=status.service.version,
            device_count=devices.count,
            online_devices=devices.online_count,
            offline_devices=devices.offline_count,
            queue_total=queue.total,
            queue_dispatched=queue.dispatched,
            queue_running=queue.running,
            queue_retry_scheduled=queue.retry_scheduled,
            queue_failed=queue.failed,
            last_error=previous.last_error if previous is not None else None,
            last_event_type=previous.last_event_type if previous is not None else None,
            last_event_detail=previous.last_event_detail if previous is not None else None,
            updated_at=utc_now(),
        )
        return replace(snapshot, last_error=None)

    def with_error(self, *, control: ControlSnapshot, message: str) -> TraySnapshot:
        return replace(
            self,
            control=control,
            level=_level_for_offline_state(control),
            connected=False,
            last_error=message,
            updated_at=utc_now(),
        )

    def with_event(self, event: RuntimeEventResponse) -> TraySnapshot:
        return replace(
            self,
            last_event_type=event.event_type,
            last_event_detail=_describe_event(event),
            updated_at=utc_now(),
        )

    @property
    def headline(self) -> str:
        return {
            TrayStatusLevel.ONLINE: "Agent healthy",
            TrayStatusLevel.BUSY: "Printing in progress",
            TrayStatusLevel.DEGRADED: "Agent needs attention",
            TrayStatusLevel.OFFLINE: "Agent unavailable",
            TrayStatusLevel.STARTING: "Agent starting",
            TrayStatusLevel.STOPPED: "Agent stopped",
        }[self.level]

    @property
    def control_line(self) -> str:
        return self.control.label

    @property
    def device_line(self) -> str:
        return f"Devices: {self.online_devices}/{self.device_count} online"

    @property
    def queue_line(self) -> str:
        return (
            f"Queue: {self.queue_total} total, "
            f"{self.queue_running + self.queue_dispatched} active, "
            f"{self.queue_failed} failed"
        )

    @property
    def status_line(self) -> str:
        version = f" v{self.service_version}" if self.service_version else ""
        return f"{self.headline}{version}"

    @property
    def error_line(self) -> str | None:
        if not self.last_error:
            return None
        return _truncate(f"Last error: {self.last_error}", 72)

    @property
    def tooltip(self) -> str:
        lines = [
            self.title,
            self.headline,
            self.control.label,
            self.device_line,
            self.queue_line,
        ]
        if self.last_error:
            lines.append(_truncate(self.last_error, 96))
        return _truncate("\n".join(lines), WINDOWS_TRAY_TOOLTIP_MAX_LENGTH)


def _level_for_status(status: SystemStatusResponse) -> TrayStatusLevel:
    queue = status.queue
    devices = status.devices
    if queue.failed or queue.retry_scheduled or devices.offline_count:
        return TrayStatusLevel.DEGRADED
    if queue.running or queue.dispatched:
        return TrayStatusLevel.BUSY
    return TrayStatusLevel.ONLINE


def _level_for_offline_state(control: ControlSnapshot) -> TrayStatusLevel:
    if control.lifecycle is LifecycleState.STARTING:
        return TrayStatusLevel.STARTING
    if control.lifecycle is LifecycleState.STOPPED:
        return TrayStatusLevel.STOPPED
    return TrayStatusLevel.OFFLINE


def _describe_event(event: RuntimeEventResponse) -> str:
    payload = event.payload
    if event.event_type.startswith("device."):
        name = payload.get("name") or payload.get("device_name") or event.resource_id
        action = event.event_type.split(".", 1)[1].replace("_", " ")
        return f"{name} {action}"
    if event.event_type == "job.failed":
        detail = payload.get("error_detail")
        if isinstance(detail, str) and detail:
            return detail
    if event.event_type.startswith("job."):
        job_id = payload.get("job_id") or event.resource_id
        action = event.event_type.split(".", 1)[1].replace("_", " ")
        return f"Job {job_id} {action}"
    return event.event_type.replace(".", " ")


def _truncate(value: str, length: int) -> str:
    if len(value) <= length:
        return value
    if length <= 3:
        return "." * max(length, 0)
    return value[: max(0, length - 3)].rstrip() + "..."


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
