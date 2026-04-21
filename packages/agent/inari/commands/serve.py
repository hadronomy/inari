from __future__ import annotations

from pathlib import Path

from ..application.container import build_container
from ..config import load_settings
from ..local_api.server import serve as serve_agent


def run_serve(config_path: Path | None) -> None:
    settings = load_settings(config_path=config_path)
    serve_agent(settings, container=build_container(settings))
