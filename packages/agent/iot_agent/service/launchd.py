from __future__ import annotations

import os
import plistlib
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
class LaunchdServiceManager:
    context: ServiceContext
    runner: CommandRunner | None = None

    @property
    def identity(self):
        return self.context.identity

    @property
    def scope(self) -> ServiceScope:
        return self.context.scope

    def install(self) -> str:
        plist_path = self._plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_parent_directories()
        config_created = ensure_service_config_file(self.context.config_path)
        plist_path.write_bytes(self.definition().content.encode("utf-8"))
        self._safe_bootout()
        self._run(["launchctl", "bootstrap", self._domain_target(), str(plist_path)])
        self._run(
            [
                "launchctl",
                "enable",
                f"{self._domain_target()}/{self.identity.launchd_label}",
            ]
        )
        message = f"Installed launchd plist at {plist_path}."
        if config_created:
            message += f" Wrote default config to {self.context.config_path}."
        return message

    def uninstall(self) -> str:
        plist_path = self._plist_path()
        self._safe_bootout()
        if plist_path.exists():
            plist_path.unlink()
        return f"Removed launchd job {self.identity.launchd_label!r}."

    def start(self) -> str:
        validate_service_config_file(self.context.config_path)
        self._run(["launchctl", "kickstart", "-k", self._service_target()])
        return f"Started launchd job {self.identity.launchd_label!r}."

    def stop(self) -> str:
        self._run(["launchctl", "stop", self._service_target()])
        return f"Stopped launchd job {self.identity.launchd_label!r}."

    def restart(self) -> str:
        validate_service_config_file(self.context.config_path)
        self._run(["launchctl", "kickstart", "-k", self._service_target()])
        return f"Restarted launchd job {self.identity.launchd_label!r}."

    def status(self) -> ServiceStatus:
        target = self._service_target()
        try:
            result = self._run(["launchctl", "print", target])
        except RuntimeError as exc:
            message = str(exc).casefold()
            if "could not find service" in message or "not found" in message:
                return ServiceStatus(
                    state=ServiceState.NOT_INSTALLED,
                    detail=f"launchd job {target!r} is not installed.",
                )
            return ServiceStatus(state=ServiceState.UNKNOWN, detail=str(exc))
        output = result.stdout.casefold()
        if "state = running" in output:
            state = ServiceState.RUNNING
        elif "state = waiting" in output:
            state = ServiceState.STOPPED
        elif "state = spawning" in output:
            state = ServiceState.STARTING
        elif "state = stopping" in output:
            state = ServiceState.STOPPING
        else:
            state = ServiceState.UNKNOWN
        return ServiceStatus(
            state=state,
            detail=f"Managing launchd job {target!r}.",
        )

    def definition(self) -> ServiceDefinition:
        log_dir = Path(self.context.settings.log_dir)
        payload = {
            "Label": self.identity.launchd_label,
            "ProgramArguments": list(self._program_arguments()),
            "RunAtLoad": True,
            "KeepAlive": True,
            "WorkingDirectory": str(self.context.working_directory),
            "StandardOutPath": str(log_dir / "service.stdout.log"),
            "StandardErrorPath": str(log_dir / "service.stderr.log"),
            "EnvironmentVariables": {
                "PYTHONUNBUFFERED": "1",
            },
        }
        content = plistlib.dumps(payload).decode("utf-8")
        return ServiceDefinition(
            format_name="launchd",
            content=content,
            path=self._plist_path(),
        )

    def _program_arguments(self) -> tuple[str, ...]:
        return (
            str(self.context.python_executable),
            "-m",
            "iot_agent",
            "serve",
            "--config",
            str(self.context.config_path),
        )

    def _plist_path(self) -> Path:
        if self.scope == "user":
            return (
                Path.home()
                / "Library"
                / "LaunchAgents"
                / f"{self.identity.launchd_label}.plist"
            )
        return Path("/Library/LaunchDaemons") / f"{self.identity.launchd_label}.plist"

    def _domain_target(self) -> str:
        if self.scope == "user":
            return f"gui/{_current_user_id()}"
        return "system"

    def _service_target(self) -> str:
        return f"{self._domain_target()}/{self.identity.launchd_label}"

    def _safe_bootout(self) -> None:
        try:
            self._run(["launchctl", "bootout", self._service_target()])
        except RuntimeError:
            return None

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
        executable = command[0] if command else "launchctl"
        raise RuntimeError(f"{executable!r} is not available on this machine.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Command failed."
        raise RuntimeError(message) from exc


def _current_user_id() -> int:
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        return int(getuid())
    return 0
