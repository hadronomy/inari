from __future__ import annotations

from .app import AgentTrayApplication
from .config import get_settings
from .logging_setup import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, log_dir=settings.log_dir)
    AgentTrayApplication.from_settings(settings).run()


if __name__ == "__main__":
    main()
