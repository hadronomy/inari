from __future__ import annotations

from pathlib import Path

import typer

from ..config import load_settings
from ..db import DatabaseMigrationError, DatabaseMigrator


def run_upgrade(config_path: Path | None) -> None:
    try:
        result = _database_migrator(config_path).ensure_current()
    except DatabaseMigrationError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if result.backup_path is not None:
        typer.echo(f"Backup: {result.backup_path}")
    if result.migrated:
        typer.echo(f"Database ready at revision {result.current_revision}")
    else:
        typer.echo(f"Database already at revision {result.current_revision}")


def run_current(config_path: Path | None) -> None:
    revision = _database_migrator(config_path).current_revision()
    typer.echo(revision or "uninitialized")


def run_backup(config_path: Path | None) -> None:
    backup_path = _database_migrator(config_path).backup_database()
    if backup_path is None:
        typer.echo("No runtime database to back up.")
        return
    typer.echo(str(backup_path))


def _database_migrator(config_path: Path | None) -> DatabaseMigrator:
    settings = load_settings(config_path=config_path)
    return DatabaseMigrator(settings.resolved_runtime_database_path)
