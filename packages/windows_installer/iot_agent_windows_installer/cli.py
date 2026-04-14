from __future__ import annotations

from pathlib import Path
import shutil

import typer

from .builder import WindowsInstallerBuilder
from .config import default_installer_config_path, load_installer_settings

app = typer.Typer(
    add_completion=False,
    help="Build Windows launchers and an MSIX package for the IoT Agent stack.",
)

ConfigOption = typer.Option(
    None,
    "--config",
    help="Path to the Windows installer TOML config.",
    exists=False,
    dir_okay=False,
    readable=True,
    resolve_path=True,
)


@app.command("init-config")
def init_config(
    destination: Path = typer.Argument(
        default_installer_config_path(),
        resolve_path=True,
        help="Where to write the example installer config.",
    ),
    *,
    force: bool = typer.Option(False, "--force", help="Overwrite the destination if it already exists."),
) -> None:
    if destination.exists() and not force:
        raise typer.BadParameter(f"{destination} already exists. Use --force to overwrite it.")
    source = _example_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    typer.echo(f"Wrote example installer config to {destination}")


@app.command("stage")
def stage(
    config: Path | None = ConfigOption,
) -> None:
    settings = load_installer_settings(config)
    result = WindowsInstallerBuilder(settings).stage()
    typer.echo(f"Tray executable: {result.tray_executable}")
    if result.service_executable is not None:
        typer.echo(f"Service executable: {result.service_executable}")
    typer.echo(f"Manifest: {result.manifest_path}")


@app.command("package")
def package(
    config: Path | None = ConfigOption,
    *,
    sign: bool | None = typer.Option(None, "--sign/--no-sign", help="Override signing for this build."),
) -> None:
    settings = load_installer_settings(config)
    output_path = WindowsInstallerBuilder(settings).package(sign=sign)
    typer.echo(str(output_path))


def main(argv: list[str] | None = None) -> None:
    app(args=argv, prog_name="iot-agent-windows-installer", standalone_mode=False)


def _example_config_path() -> Path:
    return default_installer_config_path().with_name("iot-agent-windows.example.toml")
