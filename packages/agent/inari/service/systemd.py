from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from .manager import (
    ServiceContext,
    ensure_service_config_file,
    validate_service_config_file,
)
from .models import ServiceDefinition, ServiceScope, ServiceState, ServiceStatus


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]: ...


@dataclass(slots=True)
class SystemdServiceManager:
    context: ServiceContext
    runner: CommandRunner | None = None

    @property
    def identity(self):
        return self.context.identity

    @property
    def scope(self) -> ServiceScope:
        return self.context.scope

    def install(self) -> str:
        unit_path = self._unit_path()
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_parent_directories()
        config_created = ensure_service_config_file(self.context.config_path)
        unit_path.write_text(self.definition().content, encoding="utf-8")
        self._run([*self._systemctl_base_command(), "daemon-reload"])
        self._run(
            [*self._systemctl_base_command(), "enable", self.identity.systemd_unit_name]
        )
        message = f"Installed systemd unit at {unit_path}."
        if config_created:
            message += f" Wrote default config to {self.context.config_path}."
        return message

    def uninstall(self) -> str:
        unit_path = self._unit_path()
        self._run(
            [
                *self._systemctl_base_command(),
                "disable",
                self.identity.systemd_unit_name,
            ]
        )
        if unit_path.exists():
            unit_path.unlink()
        self._run([*self._systemctl_base_command(), "daemon-reload"])
        return f"Removed systemd unit {self.identity.systemd_unit_name!r}."

    def start(self) -> str:
        validate_service_config_file(self.context.config_path)
        self._run(
            [*self._systemctl_base_command(), "start", self.identity.systemd_unit_name]
        )
        return f"Started systemd unit {self.identity.systemd_unit_name!r}."

    def stop(self) -> str:
        self._run(
            [*self._systemctl_base_command(), "stop", self.identity.systemd_unit_name]
        )
        return f"Stopped systemd unit {self.identity.systemd_unit_name!r}."

    def restart(self) -> str:
        validate_service_config_file(self.context.config_path)
        self._run(
            [
                *self._systemctl_base_command(),
                "restart",
                self.identity.systemd_unit_name,
            ]
        )
        return f"Restarted systemd unit {self.identity.systemd_unit_name!r}."

    def status(self) -> ServiceStatus:
        try:
            result = self._run(
                [
                    *self._systemctl_base_command(),
                    "show",
                    self.identity.systemd_unit_name,
                    "--property=ActiveState",
                    "--value",
                ]
            )
        except RuntimeError as exc:
            return ServiceStatus(state=ServiceState.NOT_INSTALLED, detail=str(exc))
        active_state = result.stdout.strip().casefold()
        state = {
            "active": ServiceState.RUNNING,
            "inactive": ServiceState.STOPPED,
            "activating": ServiceState.STARTING,
            "deactivating": ServiceState.STOPPING,
            "failed": ServiceState.STOPPED,
        }.get(active_state, ServiceState.UNKNOWN)
        return ServiceStatus(
            state=state,
            detail=f"Managing {self.scope} systemd unit {self.identity.systemd_unit_name!r}.",
        )

    def definition(self) -> ServiceDefinition:
        command = " ".join(
            _quote_systemd_arg(part) for part in self._program_arguments()
        )
        lines = [
            "[Unit]",
            f"Description={self.identity.display_name}",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={command}",
            f"WorkingDirectory={self.context.working_directory}",
            "Environment=PYTHONUNBUFFERED=1",
            "Restart=on-failure",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "PrivateTmp=true",
        ]
        if self.scope == "system":
            read_write_paths = sorted(
                {
                    str(self.context.settings.data_dir),
                    str(self.context.settings.log_dir),
                    str(self.context.settings.temp_dir),
                    str(self.context.settings.security_state_dir),
                    str(self.context.config_path.parent),
                }
            )
            lines.extend(
                [
                    "ProtectSystem=full",
                    "ProtectHome=true",
                    f"ReadWritePaths={' '.join(read_write_paths)}",
                ]
            )
        lines.extend(
            [
                "",
                "[Install]",
                "WantedBy=default.target"
                if self.scope == "user"
                else "WantedBy=multi-user.target",
                "",
            ]
        )
        return ServiceDefinition(
            format_name="systemd",
            content="\n".join(lines),
            path=self._unit_path(),
        )

    def _program_arguments(self) -> tuple[str, ...]:
        return (
            str(self.context.python_executable),
            "-m",
            "inari",
            "serve",
            "--config",
            str(self.context.config_path),
        )

    def _unit_path(self) -> Path:
        if self.scope == "user":
            return (
                Path.home()
                / ".config"
                / "systemd"
                / "user"
                / self.identity.systemd_unit_name
            )
        return Path("/etc/systemd/system") / self.identity.systemd_unit_name

    def _systemctl_base_command(self) -> list[str]:
        command = ["systemctl"]
        if self.scope == "user":
            command.append("--user")
        return command

    def _ensure_parent_directories(self) -> None:
        for path in (
            self.context.config_path.parent,
            self.context.settings.data_dir,
            self.context.settings.log_dir,
            self.context.settings.temp_dir,
            self.context.settings.security_state_dir,
            self.context.settings.runtime_database_path.parent
            if self.context.settings.runtime_database_path is not None
            else None,
        ):
            if path is not None:
                Path(path).mkdir(parents=True, exist_ok=True)

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        runner = self.runner or _run_command
        return runner(command)


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            check=True,
            text=True,
        )
    except FileNotFoundError as exc:
        executable = command[0] if command else "systemctl"
        raise RuntimeError(f"{executable!r} is not available on this machine.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Command failed."
        raise RuntimeError(message) from exc


def _quote_systemd_arg(value: str) -> str:
    if not value or any(character.isspace() for character in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
