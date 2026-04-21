from __future__ import annotations

from pathlib import Path

import typer

from ..config import write_default_config_file
from ..core.config_paths import PathProfile
from ..host_service.manager import resolve_service_config_path


def run_write_default(
    config_path: Path | None,
    *,
    profile: PathProfile,
    force: bool,
) -> None:
    target_path = resolve_service_config_path(config_path)
    if target_path.exists() and not force:
        typer.secho(
            f"Config file already exists: {target_path}. Pass --force to overwrite it.",
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    write_default_config_file(
        target_path,
        profile=profile,
        overwrite=force,
        schema_path=None,
    )
    typer.echo(f"Wrote default config to {target_path}")
