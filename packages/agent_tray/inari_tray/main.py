from __future__ import annotations

import argparse
from collections.abc import Sequence

from .config import get_settings
from .logging_setup import configure_logging


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, log_dir=settings.log_dir)

    from .app import AgentTrayApplication

    AgentTrayApplication.from_settings(settings, pending_invitation=args.enroll).run()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="inari-tray",
        description="Run the Inari desktop tray companion.",
    )
    parser.add_argument(
        "--enroll",
        metavar="INVITATION",
        help="Open the setup assistant with an Inari invitation link or code.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
