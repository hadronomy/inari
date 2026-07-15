from __future__ import annotations

from frozen_runtime import verify_when_requested


def _load_device_center() -> object:
    from inari_tray.app import AgentTrayApplication

    return AgentTrayApplication


def main() -> None:
    if verify_when_requested(_load_device_center):
        return

    from inari_tray.main import main as run_device_center

    run_device_center()


if __name__ == "__main__":
    main()
