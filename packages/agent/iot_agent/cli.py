from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .container import build_container
from .db import DatabaseMigrationError, DatabaseMigrator
from .main import create_app
from .config import AgentSettings, load_settings

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Run the IoT Agent service and manage its runtime database.",
)
db_app = typer.Typer(help="Inspect and upgrade the runtime database.")
app.add_typer(db_app, name="db")

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Path to the primary TOML config file.",
        exists=False,
        dir_okay=False,
        readable=True,
        resolve_path=True,
    ),
]


def _load_cli_settings(config_path: Path | None) -> AgentSettings:
    return load_settings(config_path=config_path)


def _database_migrator(config_path: Path | None) -> tuple[AgentSettings, DatabaseMigrator]:
    settings = _load_cli_settings(config_path)
    return settings, DatabaseMigrator(settings.runtime_database_path)


@app.callback(invoke_without_command=True)
def agent_cli(
    ctx: typer.Context,
    config: ConfigOption = None,
) -> None:
    if ctx.invoked_subcommand is None:
        serve(config=config)


@app.command()
def serve(
    config: ConfigOption = None,
) -> None:
    """Run the IoT Agent API server."""
    import uvicorn

    settings = _load_cli_settings(config)
    container = build_container(settings)
    uvicorn.run(
        create_app(settings=settings, container=container),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
        **(container.tls_context_factory.server_options() if container.tls_context_factory is not None else {}),
    )


@db_app.command("upgrade")
def db_upgrade(
    config: ConfigOption = None,
) -> None:
    """Apply pending runtime database migrations."""
    try:
        _, migrator = _database_migrator(config)
        result = migrator.ensure_current()
    except DatabaseMigrationError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc

    if result.backup_path is not None:
        typer.echo(f"Backup: {result.backup_path}")
    if result.migrated:
        typer.echo(f"Database ready at revision {result.current_revision}")
    else:
        typer.echo(f"Database already at revision {result.current_revision}")


@db_app.command("current")
def db_current(
    config: ConfigOption = None,
) -> None:
    """Show the current runtime database revision."""
    _, migrator = _database_migrator(config)
    revision = migrator.current_revision()
    typer.echo(revision or "uninitialized")


@db_app.command("backup")
def db_backup(
    config: ConfigOption = None,
) -> None:
    """Create a point-in-time SQLite backup."""
    _, migrator = _database_migrator(config)
    backup_path = migrator.backup_database()
    if backup_path is None:
        typer.echo("No runtime database to back up.")
        return
    typer.echo(str(backup_path))


def main(argv: list[str] | None = None) -> None:
    app(args=argv, prog_name="iot-agent", standalone_mode=False)
