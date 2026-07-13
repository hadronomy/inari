from __future__ import annotations

from pathlib import Path
from typing import cast

from inari_tray.icons import ICON_SIZE, build_packaged_app_icon, build_tray_icon
from inari_tray.models import (
    ControlMode,
    ControlSnapshot,
    LifecycleState,
    TrayLinks,
    TraySnapshot,
    TrayStatusLevel,
)


def _snapshot(*, level: TrayStatusLevel) -> TraySnapshot:
    links = TrayLinks(
        api_base_url="http://127.0.0.1:7310",
        docs_url="http://127.0.0.1:7310/docs",
        devices_url="http://127.0.0.1:7310/devices",
        jobs_url="http://127.0.0.1:7310/jobs",
        log_dir=Path("logs"),
    )
    return TraySnapshot(
        title="Inari",
        links=links,
        control=ControlSnapshot(
            mode=ControlMode.SPAWN, lifecycle=LifecycleState.RUNNING
        ),
        level=level,
        connected=True,
    )


def test_build_tray_icon_keeps_canvas_edges_transparent() -> None:
    image = build_tray_icon(_snapshot(level=TrayStatusLevel.ONLINE)).convert("RGBA")

    assert image.size == (ICON_SIZE, ICON_SIZE)
    top_left = cast(tuple[int, int, int, int], image.getpixel((0, 0)))
    top_right = cast(tuple[int, int, int, int], image.getpixel((ICON_SIZE - 1, 0)))
    bottom_left = cast(tuple[int, int, int, int], image.getpixel((0, ICON_SIZE - 1)))
    assert top_left[3] == 0
    assert top_right[3] == 0
    assert bottom_left[3] == 0


def test_build_tray_icon_places_colored_status_dot() -> None:
    image = build_tray_icon(_snapshot(level=TrayStatusLevel.OFFLINE)).convert("RGBA")

    dot_pixel = cast(
        tuple[int, int, int, int],
        image.getpixel((ICON_SIZE - 8, ICON_SIZE - 8)),
    )
    assert dot_pixel[:3] == (103, 107, 105)
    assert dot_pixel[3] == 255


def test_packaged_app_icon_uses_canonical_vermilion_tile() -> None:
    image = build_packaged_app_icon().convert("RGBA")

    crossbar = cast(tuple[int, int, int, int], image.getpixel((ICON_SIZE // 2, 28)))
    background = cast(tuple[int, int, int, int], image.getpixel((10, ICON_SIZE // 2)))
    assert crossbar[:3] == (255, 255, 255)
    assert background[:3] == (226, 61, 40)
