from __future__ import annotations

import unittest
from pathlib import Path

from iot_agent.models import RuntimeEventResponse, SystemStatusResponse

from iot_agent_tray.models import ControlMode, ControlSnapshot, LifecycleState, TrayLinks, TraySnapshot, TrayStatusLevel


class TraySnapshotTests(unittest.TestCase):
    def test_snapshot_from_status_marks_busy_when_queue_is_active(self) -> None:
        status = SystemStatusResponse.model_validate(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "IoT Agent", "version": "1.7.0a2"},
                "devices": {
                    "count": 2,
                    "online_count": 2,
                    "offline_count": 0,
                    "kind_counts": {"printer": 2},
                    "default_device_id": "dev_default",
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

        self.assertTrue(snapshot.connected)
        self.assertEqual(snapshot.level, TrayStatusLevel.BUSY)
        self.assertEqual(snapshot.queue_running, 1)
        self.assertEqual(snapshot.queue_dispatched, 1)

    def test_snapshot_with_error_preserves_counts_and_marks_offline(self) -> None:
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
                    "service": {"name": "IoT Agent", "version": "1.7.0a2"},
                    "devices": {
                        "count": 1,
                        "online_count": 1,
                        "offline_count": 0,
                        "kind_counts": {"printer": 1},
                        "default_device_id": "dev_default",
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

        self.assertFalse(offline.connected)
        self.assertEqual(offline.level, TrayStatusLevel.OFFLINE)
        self.assertEqual(offline.device_count, 1)
        self.assertEqual(offline.last_error, "Connection refused")

    def test_snapshot_with_event_captures_humanized_detail(self) -> None:
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

        self.assertEqual(updated.last_event_type, "job.failed")
        self.assertEqual(updated.last_event_detail, "Printer offline")


def _links() -> TrayLinks:
    return TrayLinks(
        api_base_url="http://127.0.0.1:7310",
        docs_url="http://127.0.0.1:7310/docs",
        devices_url="http://127.0.0.1:7310/devices",
        jobs_url="http://127.0.0.1:7310/jobs",
        log_dir=Path("./logs"),
    )


if __name__ == "__main__":
    unittest.main()
