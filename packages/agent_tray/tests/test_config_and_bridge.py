from __future__ import annotations

import unittest

from iot_agent_tray.bridge import SpawnedProcessAgentBridge, build_control_bridge
from iot_agent_tray.config import TraySettings
from iot_agent_tray.models import ControlMode, LifecycleState


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


if __name__ == "__main__":
    unittest.main()
