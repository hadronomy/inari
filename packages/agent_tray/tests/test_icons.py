from __future__ import annotations

from pathlib import Path
import unittest

from iot_agent_tray.icons import ICON_SIZE, build_tray_icon
from iot_agent_tray.models import ControlMode, ControlSnapshot, LifecycleState, TrayLinks, TraySnapshot, TrayStatusLevel


def _snapshot(*, level: TrayStatusLevel) -> TraySnapshot:
    links = TrayLinks(
        api_base_url="http://127.0.0.1:7310",
        docs_url="http://127.0.0.1:7310/docs",
        devices_url="http://127.0.0.1:7310/devices",
        jobs_url="http://127.0.0.1:7310/jobs",
        log_dir=Path("logs"),
    )
    return TraySnapshot(
        title="IoT Agent",
        links=links,
        control=ControlSnapshot(mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING),
        level=level,
        connected=True,
    )


class TrayIconTests(unittest.TestCase):
    def test_build_tray_icon_keeps_canvas_edges_transparent(self) -> None:
        image = build_tray_icon(_snapshot(level=TrayStatusLevel.ONLINE))

        self.assertEqual(image.size, (ICON_SIZE, ICON_SIZE))
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((ICON_SIZE - 1, 0))[3], 0)
        self.assertEqual(image.getpixel((0, ICON_SIZE - 1))[3], 0)

    def test_build_tray_icon_places_colored_status_dot(self) -> None:
        image = build_tray_icon(_snapshot(level=TrayStatusLevel.OFFLINE))

        dot_pixel = image.getpixel((ICON_SIZE - 8, ICON_SIZE - 8))
        self.assertEqual(dot_pixel[:3], (240, 87, 113))
        self.assertEqual(dot_pixel[3], 255)


if __name__ == "__main__":
    unittest.main()
