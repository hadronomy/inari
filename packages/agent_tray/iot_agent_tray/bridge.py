from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from .config import TraySettings
from .models import ControlMode, ControlSnapshot, LifecycleState


class CommandRunner(Protocol):
    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]: ...


class AgentControlBridge:
    mode: ControlMode

    def query_state(self) -> ControlSnapshot:
        raise NotImplementedError

    def mark_ready(self) -> None:
        return None

    def start(self) -> str:
        raise RuntimeError("Start is not supported by this control mode.")

    def stop(self) -> str:
        raise RuntimeError("Stop is not supported by this control mode.")

    def restart(self) -> str:
        raise RuntimeError("Restart is not supported by this control mode.")

    def shutdown(self) -> None:
        return None


class UnsupportedServiceAgentBridge(AgentControlBridge):
    mode = ControlMode.SERVICE

    def __init__(self, detail: str) -> None:
        self._detail = detail

    def query_state(self) -> ControlSnapshot:
        return ControlSnapshot(
            mode=self.mode,
            lifecycle=LifecycleState.UNKNOWN,
            detail=self._detail,
            can_start=False,
            can_stop=False,
            can_restart=False,
        )


class MonitorAgentBridge(AgentControlBridge):
    mode = ControlMode.MONITOR

    def query_state(self) -> ControlSnapshot:
        return ControlSnapshot(
            mode=self.mode,
            lifecycle=LifecycleState.UNKNOWN,
            detail="Monitoring an external agent instance.",
        )


class SpawnedProcessAgentBridge(AgentControlBridge):
    mode = ControlMode.SPAWN

    def __init__(
        self,
        settings: TraySettings,
        *,
        process_factory: Callable[..., Any] | None = None,
        launch_command: Sequence[str] | None = None,
        working_directory: Path | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.settings = settings
        self._process_factory = process_factory or subprocess.Popen
        self._launch_command = (
            tuple(launch_command) if launch_command is not None else None
        )
        self._working_directory = working_directory or _default_working_directory()
        self._clock = clock or time.monotonic
        self._launch_log_path = settings.log_dir / "agent-launch.log"
        self._lock = threading.Lock()
        self._process: Any | None = None
        self._process_output_handle: Any | None = None
        self._startup_started_at: float | None = None
        self._ready = False

    def query_state(self) -> ControlSnapshot:
        with self._lock:
            process = self._process
            if process is None:
                return ControlSnapshot(
                    mode=self.mode,
                    lifecycle=LifecycleState.STOPPED,
                    detail="Ready to launch a local agent process.",
                    can_start=True,
                    can_stop=False,
                    can_restart=False,
                    managed_by_tray=False,
                )

            exit_code = process.poll()
            if exit_code is None:
                if self._is_starting():
                    return ControlSnapshot(
                        mode=self.mode,
                        lifecycle=LifecycleState.STARTING,
                        detail="Waiting for the local agent API to become ready.",
                        can_start=False,
                        can_stop=True,
                        can_restart=True,
                        managed_by_tray=True,
                    )
                detail = "Managing a local background agent process."
                if getattr(process, "pid", None) is not None:
                    detail = f"Managing local agent process PID {process.pid}."
                return ControlSnapshot(
                    mode=self.mode,
                    lifecycle=LifecycleState.RUNNING,
                    detail=detail,
                    can_start=False,
                    can_stop=True,
                    can_restart=True,
                    managed_by_tray=True,
                )

            self._startup_started_at = None
            self._ready = False
            self._close_process_output_handle()
            detail = f"Last managed process exited with code {exit_code}. See {self._launch_log_path}."
            return ControlSnapshot(
                mode=self.mode,
                lifecycle=LifecycleState.STOPPED,
                detail=detail,
                can_start=True,
                can_stop=False,
                can_restart=True,
                managed_by_tray=False,
            )

    def mark_ready(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._ready = True

    def start(self) -> str:
        with self._lock:
            if self._is_running():
                return "The local agent process is already running."
            self._process = None
            self._startup_started_at = None
            self._ready = False
            self._close_process_output_handle()
            self.settings.log_dir.mkdir(parents=True, exist_ok=True)
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
                creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            command = self._resolve_launch_command()
            output_handle = self._launch_log_path.open("a", encoding="utf-8")
            output_handle.write(
                f"\n=== Starting IoT Agent at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n"
            )
            output_handle.write(f"Command: {' '.join(command)}\n")
            output_handle.flush()
            self._process_output_handle = output_handle
            self._process = self._process_factory(
                list(command),
                cwd=str(self._working_directory),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=output_handle,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            self._startup_started_at = self._clock()
            self._ready = False
            time.sleep(0.5)
            exit_code = self._process.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"The local agent exited immediately with code {exit_code}. See {self._launch_log_path}."
                )
        return "Started the local agent process."

    def stop(self) -> str:
        with self._lock:
            if not self._is_running():
                raise RuntimeError(
                    "There is no tray-managed local agent process to stop."
                )
            process = self._process
            assert process is not None
            process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
                process.wait(timeout=5)
            self._process = None
            self._startup_started_at = None
            self._ready = False
            self._close_process_output_handle()
        return "Stopped the local agent process."

    def restart(self) -> str:
        if self._is_running():
            self.stop()
        self.start()
        return "Restarted the local agent process."

    def shutdown(self) -> None:
        if self.settings.shutdown_started_process_on_exit and self._is_running():
            try:
                self.stop()
            except Exception:
                return None

    def _is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _is_starting(self) -> bool:
        if self._ready or self._startup_started_at is None:
            return False
        return (
            self._clock() - self._startup_started_at
        ) < self.settings.startup_grace_period_seconds

    def _resolve_launch_command(self) -> tuple[str, ...]:
        if self._launch_command is not None:
            return self._launch_command
        if _supports_module_launch():
            return (sys.executable, "-m", "iot_agent")
        console_script = shutil.which("iot-agent")
        if console_script is not None:
            return (console_script,)
        agent_workspace = _detect_agent_workspace(self._working_directory)
        uv_executable = shutil.which("uv")
        if agent_workspace is not None and uv_executable is not None:
            return (
                uv_executable,
                "run",
                "--directory",
                str(agent_workspace),
                "iot-agent",
            )
        return (sys.executable, "-m", "iot_agent")

    def _close_process_output_handle(self) -> None:
        handle = self._process_output_handle
        if handle is None:
            return
        try:
            handle.close()
        except Exception:
            return
        finally:
            self._process_output_handle = None


class SubprocessServiceAgentBridge(AgentControlBridge):
    mode = ControlMode.SERVICE
    manager_name = "service"

    def __init__(
        self, settings: TraySettings, *, runner: CommandRunner | None = None
    ) -> None:
        self.settings = settings
        self._runner = runner or _run_command

    def query_state(self) -> ControlSnapshot:
        try:
            lifecycle, detail = self._query_lifecycle_state()
            return ControlSnapshot(
                mode=self.mode,
                lifecycle=lifecycle,
                detail=detail,
                can_start=lifecycle in {LifecycleState.STOPPED, LifecycleState.UNKNOWN},
                can_stop=lifecycle in {LifecycleState.RUNNING, LifecycleState.STARTING},
                can_restart=lifecycle
                in {
                    LifecycleState.RUNNING,
                    LifecycleState.STARTING,
                    LifecycleState.STOPPED,
                },
            )
        except Exception as exc:
            return ControlSnapshot(
                mode=self.mode,
                lifecycle=LifecycleState.UNKNOWN,
                detail=str(exc),
            )

    def start(self) -> str:
        lifecycle, _ = self._query_lifecycle_state()
        if lifecycle is LifecycleState.RUNNING:
            return f"{self.manager_name.title()} {self.settings.service_name!r} is already running."
        self._run(self._start_command())
        return (
            f"Start requested for {self.manager_name} {self.settings.service_name!r}."
        )

    def stop(self) -> str:
        lifecycle, _ = self._query_lifecycle_state()
        if lifecycle is LifecycleState.STOPPED:
            return f"{self.manager_name.title()} {self.settings.service_name!r} is already stopped."
        self._run(self._stop_command())
        return f"Stop requested for {self.manager_name} {self.settings.service_name!r}."

    def restart(self) -> str:
        self._run(self._restart_command())
        return (
            f"Restart requested for {self.manager_name} {self.settings.service_name!r}."
        )

    def _query_lifecycle_state(self) -> tuple[LifecycleState, str]:
        raise NotImplementedError

    def _start_command(self) -> Sequence[str]:
        raise NotImplementedError

    def _stop_command(self) -> Sequence[str]:
        raise NotImplementedError

    def _restart_command(self) -> Sequence[str]:
        raise NotImplementedError

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return self._runner(command)


class WindowsServiceAgentBridge(AgentControlBridge):
    mode = ControlMode.SERVICE

    def __init__(self, settings: TraySettings) -> None:
        self.settings = settings

    def query_state(self) -> ControlSnapshot:
        try:
            lifecycle = self._query_lifecycle_state()
            return ControlSnapshot(
                mode=self.mode,
                lifecycle=lifecycle,
                detail=f"Managing Windows service {self.settings.service_name!r}.",
                can_start=lifecycle in {LifecycleState.STOPPED, LifecycleState.UNKNOWN},
                can_stop=lifecycle in {LifecycleState.RUNNING, LifecycleState.STARTING},
                can_restart=lifecycle
                in {
                    LifecycleState.RUNNING,
                    LifecycleState.STARTING,
                    LifecycleState.STOPPED,
                },
            )
        except Exception as exc:
            return ControlSnapshot(
                mode=self.mode,
                lifecycle=LifecycleState.UNKNOWN,
                detail=str(exc),
            )

    def start(self) -> str:
        win32serviceutil = self._import_service_util()
        lifecycle = self._query_lifecycle_state()
        if lifecycle is LifecycleState.RUNNING:
            return f"Windows service {self.settings.service_name!r} is already running."
        win32serviceutil.StartService(self.settings.service_name)
        return f"Start requested for Windows service {self.settings.service_name!r}."

    def stop(self) -> str:
        win32serviceutil = self._import_service_util()
        lifecycle = self._query_lifecycle_state()
        if lifecycle is LifecycleState.STOPPED:
            return f"Windows service {self.settings.service_name!r} is already stopped."
        win32serviceutil.StopService(self.settings.service_name)
        return f"Stop requested for Windows service {self.settings.service_name!r}."

    def restart(self) -> str:
        lifecycle = self._query_lifecycle_state()
        if lifecycle in {LifecycleState.RUNNING, LifecycleState.STARTING}:
            self.stop()
            self._wait_for(LifecycleState.STOPPED, timeout_seconds=20.0)
        self.start()
        return f"Restart requested for Windows service {self.settings.service_name!r}."

    def _query_lifecycle_state(self) -> LifecycleState:
        win32service, win32serviceutil = self._import_service_modules()
        status = win32serviceutil.QueryServiceStatus(self.settings.service_name)
        raw_state = int(status[1])
        mapping = {
            int(win32service.SERVICE_RUNNING): LifecycleState.RUNNING,
            int(win32service.SERVICE_STOPPED): LifecycleState.STOPPED,
            int(win32service.SERVICE_START_PENDING): LifecycleState.STARTING,
            int(win32service.SERVICE_STOP_PENDING): LifecycleState.STOPPING,
        }
        return mapping.get(raw_state, LifecycleState.UNKNOWN)

    def _wait_for(self, desired: LifecycleState, *, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self._query_lifecycle_state() is desired:
                return
            time.sleep(0.5)
        raise TimeoutError(f"Timed out while waiting for {desired.value}.")

    def _import_service_util(self):
        _, win32serviceutil = self._import_service_modules()
        return win32serviceutil

    def _import_service_modules(self):
        if sys.platform != "win32":
            raise RuntimeError("Windows service control is only available on Windows.")
        import win32service  # type: ignore[import-not-found]
        import win32serviceutil  # type: ignore[import-not-found]

        return win32service, win32serviceutil


class SystemdAgentBridge(SubprocessServiceAgentBridge):
    manager_name = "systemd service"

    def _query_lifecycle_state(self) -> tuple[LifecycleState, str]:
        result = self._run(
            [
                *self._systemctl_base_command(),
                "show",
                self.settings.service_name,
                "--property=ActiveState",
                "--value",
            ]
        )
        active_state = result.stdout.strip().casefold()
        lifecycle = {
            "active": LifecycleState.RUNNING,
            "inactive": LifecycleState.STOPPED,
            "activating": LifecycleState.STARTING,
            "deactivating": LifecycleState.STOPPING,
            "failed": LifecycleState.STOPPED,
        }.get(active_state, LifecycleState.UNKNOWN)
        scope = "user" if self.settings.service_scope == "user" else "system"
        return (
            lifecycle,
            f"Managing {scope} systemd service {self.settings.service_name!r}.",
        )

    def _start_command(self) -> Sequence[str]:
        return [*self._systemctl_base_command(), "start", self.settings.service_name]

    def _stop_command(self) -> Sequence[str]:
        return [*self._systemctl_base_command(), "stop", self.settings.service_name]

    def _restart_command(self) -> Sequence[str]:
        return [*self._systemctl_base_command(), "restart", self.settings.service_name]

    def _systemctl_base_command(self) -> list[str]:
        command = ["systemctl"]
        if self.settings.service_scope == "user":
            command.append("--user")
        return command


class LaunchdAgentBridge(SubprocessServiceAgentBridge):
    manager_name = "launchd service"

    def _query_lifecycle_state(self) -> tuple[LifecycleState, str]:
        target = self._launchd_target()
        try:
            result = self._run(["launchctl", "print", target])
        except RuntimeError as exc:
            message = str(exc).casefold()
            if "could not find service" in message or "not found" in message:
                return LifecycleState.STOPPED, f"Managing launchd job {target!r}."
            raise
        output = result.stdout.casefold()
        if "state = running" in output:
            lifecycle = LifecycleState.RUNNING
        elif "state = waiting" in output:
            lifecycle = LifecycleState.STOPPED
        elif "state = spawning" in output:
            lifecycle = LifecycleState.STARTING
        elif "state = stopping" in output:
            lifecycle = LifecycleState.STOPPING
        else:
            lifecycle = LifecycleState.UNKNOWN
        return lifecycle, f"Managing launchd job {target!r}."

    def _start_command(self) -> Sequence[str]:
        return ["launchctl", "kickstart", "-k", self._launchd_target()]

    def _stop_command(self) -> Sequence[str]:
        return ["launchctl", "stop", self._launchd_target()]

    def _restart_command(self) -> Sequence[str]:
        return ["launchctl", "kickstart", "-k", self._launchd_target()]

    def _launchd_target(self) -> str:
        if self.settings.service_scope == "system":
            return f"system/{self.settings.service_name}"
        return f"gui/{_current_user_id()}/{self.settings.service_name}"


def build_control_bridge(
    settings: TraySettings, *, platform_name: str | None = None
) -> AgentControlBridge:
    current_platform = platform_name or platform.system()
    if settings.control_mode == "spawn":
        return SpawnedProcessAgentBridge(settings)
    if settings.control_mode == "service":
        if current_platform == "Windows":
            return WindowsServiceAgentBridge(settings)
        if current_platform == "Linux":
            return SystemdAgentBridge(settings)
        if current_platform == "Darwin":
            return LaunchdAgentBridge(settings)
        return UnsupportedServiceAgentBridge(
            f"Service control mode is not supported on {current_platform}."
        )
    return MonitorAgentBridge()


def _default_working_directory() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists() and (parent / "packages").exists():
            return parent
    return Path.cwd()


def _detect_agent_workspace(working_directory: Path) -> Path | None:
    candidates = [working_directory, *working_directory.parents]
    for candidate in candidates:
        agent_workspace = candidate / "packages" / "agent"
        if (agent_workspace / "pyproject.toml").exists():
            return agent_workspace
    return None


def _supports_module_launch() -> bool:
    return importlib.util.find_spec("iot_agent.__main__") is not None


def _run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            check=True,
            text=True,
        )
    except FileNotFoundError as exc:
        executable = command[0] if command else "command"
        raise RuntimeError(f"{executable!r} is not available on this machine.") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or "Command failed."
        raise RuntimeError(message) from exc


def _current_user_id() -> int:
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        return int(getuid())
    return 0
