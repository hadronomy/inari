from __future__ import annotations

import unittest

from iot_agent.models import SystemStatusResponse
from iot_agent_tray.app import AgentTrayApplication
from iot_agent_tray.bridge import MonitorAgentBridge, SpawnedProcessAgentBridge, build_control_bridge
from iot_agent_tray.config import TraySettings
from iot_agent_tray.models import ControlMode, LifecycleState


class TrayApplicationTests(unittest.TestCase):
    def test_setup_background_marks_icon_visible(self) -> None:
        application = AgentTrayApplication(
            TraySettings(),
            client=FakeTrayClient(),
            bridge=MonitorAgentBridge(),
        )

        icon = FakeIcon()
        application._setup_background(icon)

        self.assertTrue(icon.visible)
        self.assertEqual(len(application._threads), 2)
        application._stop_event.set()
        for thread in application._threads:
            thread.join(timeout=1.0)


class TraySettingsTests(unittest.TestCase):
    def test_settings_derive_related_agent_urls(self) -> None:
        settings = TraySettings(agent_api_base_url="http://localhost:7310/")

        self.assertEqual(settings.agent_api_base_url, "http://localhost:7310")
        self.assertEqual(settings.agent_docs_url, "http://localhost:7310/docs")
        self.assertEqual(settings.agent_devices_url, "http://localhost:7310/devices")
        self.assertEqual(settings.agent_jobs_url, "http://localhost:7310/jobs")
        self.assertEqual(settings.agent_events_url, "ws://localhost:7310/events")


class SpawnedProcessBridgeTests(unittest.TestCase):
    def test_spawned_process_bridge_manages_process_lifecycle(self) -> None:
        created: list[FakeProcess] = []

        def process_factory(*args, **kwargs):
            process = FakeProcess()
            created.append(process)
            return process

        bridge = SpawnedProcessAgentBridge(
            TraySettings(control_mode="spawn"),
            process_factory=process_factory,
        )

        start_message = bridge.start()
        running = bridge.query_state()
        stop_message = bridge.stop()
        stopped = bridge.query_state()

        self.assertEqual(start_message, "Started the local agent process.")
        self.assertEqual(running.mode, ControlMode.SPAWN)
        self.assertEqual(running.lifecycle, LifecycleState.RUNNING)
        self.assertTrue(running.can_stop)
        self.assertEqual(stop_message, "Stopped the local agent process.")
        self.assertEqual(stopped.lifecycle, LifecycleState.UNKNOWN)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].terminated)

    def test_build_control_bridge_uses_monitor_fallback(self) -> None:
        bridge = build_control_bridge(TraySettings(control_mode="monitor"))

        state = bridge.query_state()

        self.assertEqual(state.mode, ControlMode.MONITOR)
        self.assertEqual(state.lifecycle, LifecycleState.UNKNOWN)


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class FakeIcon:
    def __init__(self) -> None:
        self.visible = False


class FakeTrayClient:
    def get_status(self) -> SystemStatusResponse:
        return SystemStatusResponse.model_validate(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "IoT Agent", "version": "1.7.0a2"},
                "devices": {
                    "count": 0,
                    "online_count": 0,
                    "offline_count": 0,
                    "kind_counts": {},
                    "default_device_id": None,
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
        )

    def iter_events(self, stop_event):
        return iter(())


if __name__ == "__main__":
    unittest.main()
