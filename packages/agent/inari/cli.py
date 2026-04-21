from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .core.config_paths import PathProfile
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
    from .commands.serve import run_serve

    run_serve(config)


@db_app.command("upgrade")
def db_upgrade(
    config: ConfigOption = None,
) -> None:
    """Apply pending runtime database migrations."""
    from .commands.db import run_upgrade

    run_upgrade(config)


@db_app.command("current")
def db_current(
    config: ConfigOption = None,
) -> None:
    """Show the current runtime database revision."""
    from .commands.db import run_current

    run_current(config)


@db_app.command("backup")
def db_backup(
    config: ConfigOption = None,
) -> None:
    """Create a point-in-time SQLite backup."""
    from .commands.db import run_backup

    run_backup(config)


@service_app.command("install")
def service_install(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Install the Inari as a platform-native service."""
    from .commands.service import run_install

    run_install(config, scope)


@service_app.command("uninstall")
def service_uninstall(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Remove the Inari platform service definition."""
    from .commands.service import run_uninstall

    run_uninstall(config, scope)


@service_app.command("start")
def service_start(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Start the platform service."""
    from .commands.service import run_start

    run_start(config, scope)


@service_app.command("stop")
def service_stop(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Stop the platform service."""
    from .commands.service import run_stop

    run_stop(config, scope)


@service_app.command("restart")
def service_restart(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Restart the platform service."""
    from .commands.service import run_restart

    run_restart(config, scope)


@service_app.command("status")
def service_status(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Show the current platform service state."""
    from .commands.service import run_status

    run_status(config, scope)


@service_app.command("print-definition")
def service_print_definition(
    config: ConfigOption = None,
    scope: ScopeOption = DEFAULT_SERVICE_SCOPE,
) -> None:
    """Print the platform-native service definition without installing it."""
    from .commands.service import run_print_definition

    run_print_definition(config, scope)


@config_app.command("write-default")
def config_write_default(
    config: ConfigOption = None,
    profile: PathProfileOption = "production",
    force: ForceOption = False,
) -> None:
    """Write a sensible default config file for the agent."""
    from .commands.config import run_write_default

    run_write_default(config, profile=profile, force=force)


def main(argv: list[str] | None = None) -> None:
    app(args=argv, prog_name="inari", standalone_mode=False)
