from __future__ import annotations

from frozen_runtime import verify_when_requested


def _load_agent_service() -> object:
    from inari.host_service import windows_entrypoint

    return windows_entrypoint


def main() -> None:
    if verify_when_requested(_load_agent_service):
        return

    from inari.host_service.windows_entrypoint import main as run_agent_service

    run_agent_service()


if __name__ == "__main__":
    main()
