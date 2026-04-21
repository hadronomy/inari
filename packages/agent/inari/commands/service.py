from __future__ import annotations

from pathlib import Path

import typer

from ..host_service.manager import build_service_manager, load_service_settings
from ..host_service.models import ServiceScope


def run_install(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, resolved_config_path, manager = _service_manager(config_path, scope)
        typer.echo(manager.install())
        typer.echo(f"Config: {resolved_config_path}")
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)


def run_uninstall(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        typer.echo(manager.uninstall())
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)


def run_start(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        typer.echo(manager.start())
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)


def run_stop(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        typer.echo(manager.stop())
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)


def run_restart(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        typer.echo(manager.restart())
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)


def run_status(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        status = manager.status()
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)
    typer.echo(f"State: {status.state.value}")
    typer.echo(f"Detail: {status.detail}")


def run_print_definition(config_path: Path | None, scope: ServiceScope) -> None:
    try:
        _, _, manager = _service_manager(config_path, scope)
        definition = manager.definition()
    except RuntimeError as exc:
        _exit_with_runtime_error(exc)
    if definition.path is not None:
        typer.echo(f"# Path: {definition.path}")
    typer.echo(definition.content, nl=not definition.content.endswith("\n"))


def _service_manager(config_path: Path | None, scope: ServiceScope):
    settings, resolved_config_path = load_service_settings(config_path)
    manager = build_service_manager(
        settings, config_path=resolved_config_path, scope=scope
    )
    return settings, resolved_config_path, manager


def _exit_with_runtime_error(exc: RuntimeError) -> None:
    typer.secho(str(exc), err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1) from exc
