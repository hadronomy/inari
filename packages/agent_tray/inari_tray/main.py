from __future__ import annotations

import argparse
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from .config import get_settings
from .logging_setup import configure_logging
from .single_instance import (
    ActivationRequest,
    DeviceCenterInstance,
    parse_enrollment_link,
)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, log_dir=settings.log_dir)

    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication([settings.title])
    request = ActivationRequest(invitation=args.invitation)
    instance = DeviceCenterInstance.acquire(request)
    if instance is None:
        return

    from .app import AgentTrayApplication

    AgentTrayApplication.from_settings(
        settings,
        pending_invitation=args.invitation,
        desktop_instance=instance,
    ).run()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="inari-tray",
        description="Run the Inari desktop tray companion.",
    )
    parser.add_argument(
        "invitation",
        nargs="?",
        type=_enrollment_link_argument,
        help="An inari:// enrollment link to open in the setup assistant.",
    )
    return parser.parse_args(argv)


def _enrollment_link_argument(value: str) -> str:
    try:
        return parse_enrollment_link(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


if __name__ == "__main__":
    main()
