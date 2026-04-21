from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .application.container import build_container
from .db import DatabaseMigrationError, DatabaseMigrator
from .config import AgentSettings, PathProfile, load_settings, write_default_config_file
from .local_api.server import serve as serve_agent
from .host_service.manager import (
    build_service_manager,
    load_service_settings,
    resolve_service_config_path,
)
from .host_service.models import DEFAULT_SERVICE_SCOPE, ServiceScope

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Run the Inari service and manage its runtime database.",
)
db_app = typer.Typer(help="Inspect and upgrade the runtime database.")
service_app = typer.Typer(help="Install and manage the Inari as a platform service.")
config_app = typer.Typer(help="Generate and write agent configuration files.")
app.add_typer(db_app, name="db")
app.add_typer(service_app, name="service")
app.add_typer(config_app, name="config")

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
ScopeOption = Annotated[
    ServiceScope,
    typer.Option(
        "--scope",
        help="Service scope for launchd/systemd. Windows always uses system scope.",
    ),
]
PathProfileOption = Annotated[
    PathProfile,
    typer.Option(
        "--profile",
        help="Path profile to bake into a generated default config.",
    ),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Overwrite the target file if it already exists.",
    ),
]


def _load_cli_settings(config_path: Path | None) -> AgentSettings:
    return load_settings(config_path=config_path)


def _database_migrator(
    config_path: Path | None,
) -> tuple[AgentSettings, DatabaseMigrator]:
    settings = _load_cli_settings(config_path)
    return settings, DatabaseMigrator(settings.resolved_runtime_database_path)


def _service_manager(config_path: Path | None, scope: ServiceScope):
    settings, resolved_config_path = load_service_settings(config_path)
    manager = build_service_manager(
        settings, config_path=resolved_config_path, scope=scope
    )
    return settings, resolved_config_path, manager


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
    """Run the Inari API server."""
    settings = _load_cli_settings(config)
    serve_agent(settings, container=build_container(settings))


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
    app(args=argv, prog_name="inari", standalone_mode=False)


@service_app.command("install")
def service_install(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Install the Inari as a platform-native service."""
    try:
        _, resolved_config_path, manager = _service_manager(config, scope)
        typer.echo(manager.install())
        typer.echo(f"Config: {resolved_config_path}")
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@service_app.command("uninstall")
def service_uninstall(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Remove the Inari platform service definition."""
    try:
        _, _, manager = _service_manager(config, scope)
        typer.echo(manager.uninstall())
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@service_app.command("start")
def service_start(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Start the platform service."""
    try:
        _, _, manager = _service_manager(config, scope)
        typer.echo(manager.start())
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@service_app.command("stop")
def service_stop(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Stop the platform service."""
    try:
        _, _, manager = _service_manager(config, scope)
        typer.echo(manager.stop())
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@service_app.command("restart")
def service_restart(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Restart the platform service."""
    try:
        _, _, manager = _service_manager(config, scope)
        typer.echo(manager.restart())
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc


@service_app.command("status")
def service_status(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Show the current platform service state."""
    try:
        _, _, manager = _service_manager(config, scope)
        status = manager.status()
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    typer.echo(f"State: {status.state.value}")
    typer.echo(f"Detail: {status.detail}")


@service_app.command("print-definition")
def service_print_definition(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Print the platform-native service definition without installing it."""
    try:
        _, _, manager = _service_manager(config, scope)
        definition = manager.definition()
    except RuntimeError as exc:
        typer.secho(str(exc), err=True, fg=typer.colors.RED)
        raise typer.Exit(code=1) from exc
    if definition.path is not None:
        typer.echo(f"# Path: {definition.path}")
    typer.echo(definition.content, nl=not definition.content.endswith("\n"))


@config_app.command("write-default")
def config_write_default(
    config: ConfigOption = None,
    profile: PathProfileOption = "production",
    force: ForceOption = False,
) -> None:
    """Write a sensible default config file for the agent."""
    target_path = resolve_service_config_path(config)
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
