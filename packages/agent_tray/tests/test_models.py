from __future__ import annotations

from pathlib import Path

from iot_agent.models import RuntimeEventResponse, SystemStatusResponse
from iot_agent.version import API_VERSION

from iot_agent_tray.models import ControlMode, ControlSnapshot, LifecycleState, TrayLinks, TraySnapshot, TrayStatusLevel


def test_snapshot_from_status_marks_busy_when_queue_is_active() -> None:
    status = SystemStatusResponse.model_validate(
        {
            "ok": True,
            "status": "healthy",
            "service": {"name": "IoT Agent", "version": API_VERSION},
            "devices": {
                "count": 2,
                "online_count": 2,
                "offline_count": 0,
                "kind_counts": {"printer": 2},
                "default_device": {"id": "dev_default", "name": "Kitchen Printer"},
            },
            "queue": {
                "total": 3,
                "queued": 1,
                "dispatched": 1,
                "running": 1,
                "retry_scheduled": 0,
                "succeeded": 0,
                "failed": 0,
                "cancelled": 0,
            },
            "supported_content_kinds": ["text"],
            "supported_device_commands": ["print_test_page"],
        }
    )

    snapshot = TraySnapshot.from_status(
        title="IoT Agent",
        links=_links(),
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
        status=status,
    )

    assert snapshot.connected
    assert snapshot.level is TrayStatusLevel.BUSY
    assert snapshot.queue_running == 1
    assert snapshot.queue_dispatched == 1


def test_snapshot_with_error_preserves_counts_and_marks_offline() -> None:
    snapshot = TraySnapshot.initial(
        title="IoT Agent",
        links=_links(),
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
    )
    snapshot = TraySnapshot.from_status(
        title="IoT Agent",
        links=_links(),
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
        status=SystemStatusResponse.model_validate(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "IoT Agent", "version": API_VERSION},
                "devices": {
                    "count": 1,
                    "online_count": 1,
                    "offline_count": 0,
                    "kind_counts": {"printer": 1},
                    "default_device": {"id": "dev_default", "name": "Kitchen Printer"},
                },
                "queue": {
                    "total": 0,
                    "queued": 0,
                    "dispatched": 0,
                    "running": 0,
                    "retry_scheduled": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "cancelled": 0,
                },
                "supported_content_kinds": ["text"],
                "supported_device_commands": ["print_test_page"],
            }
        ),
    )

    offline = snapshot.with_error(
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.UNKNOWN),
        message="Connection refused",
    )

    assert not offline.connected
    assert offline.level is TrayStatusLevel.OFFLINE
    assert offline.device_count == 1
    assert offline.last_error == "Connection refused"


def test_snapshot_with_event_captures_humanized_detail() -> None:
    snapshot = TraySnapshot.initial(
        title="IoT Agent",
        links=_links(),
        control=ControlSnapshot(mode=ControlMode.MONITOR),
    )
    event = RuntimeEventResponse.model_validate(
        {
            "sequence": 3,
            "resource_kind": "job",
            "resource_id": "job_123",
            "event_type": "job.failed",
            "occurred_at": "2026-04-11T10:00:00Z",
            "payload": {"job_id": "job_123", "error_detail": "Printer offline"},
        }
    )

    updated = snapshot.with_event(event)

    assert updated.last_event_type == "job.failed"
    assert updated.last_event_detail == "Printer offline"


def test_tooltip_is_capped_to_windows_limit() -> None:
    snapshot = TraySnapshot.initial(
        title="IoT Agent",
        links=_links(),
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
    ).with_error(
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
        message="The local agent reported a very long startup failure message that should never overflow the Windows tray tooltip limit even when debugging information is present.",
    )

    assert len(snapshot.tooltip) <= 128
    assert snapshot.tooltip.endswith("...")


def _links() -> TrayLinks:
    return TrayLinks(
        api_base_url="http://127.0.0.1:7310",
        docs_url="http://127.0.0.1:7310/docs",
        devices_url="http://127.0.0.1:7310/devices",
        jobs_url="http://127.0.0.1:7310/jobs",
        log_dir=Path("./logs"),
    )
