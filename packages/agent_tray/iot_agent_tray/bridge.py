from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import TraySettings
from .models import ControlMode, ControlSnapshot, LifecycleState


class AgentControlBridge:
    mode: ControlMode

    def query_state(self) -> ControlSnapshot:
        raise NotImplementedError

    def start(self) -> str:
        raise RuntimeError("Start is not supported by this control mode.")

    def stop(self) -> str:
        raise RuntimeError("Stop is not supported by this control mode.")

    def restart(self) -> str:
        raise RuntimeError("Restart is not supported by this control mode.")

    def shutdown(self) -> None:
        return None


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
        working_directory: Path | None = None,
    ) -> None:
        self.settings = settings
        self._process_factory = process_factory or subprocess.Popen
        self._working_directory = working_directory or Path.cwd()
        self._lock = threading.Lock()
        self._process: Any | None = None

    def query_state(self) -> ControlSnapshot:
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

        detail = f"Last managed process exited with code {exit_code}."
        return ControlSnapshot(
            mode=self.mode,
            lifecycle=LifecycleState.STOPPED,
            detail=detail,
            can_start=True,
            can_stop=False,
            can_restart=True,
            managed_by_tray=False,
        )

    def start(self) -> str:
        with self._lock:
            if self._is_running():
                return "The local agent process is already running."
            self._process = None
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
                creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self._process = self._process_factory(
                [sys.executable, "-m", "iot_agent.main"],
                cwd=str(self._working_directory),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        return "Started the local agent process."

    def stop(self) -> str:
        with self._lock:
            if not self._is_running():
                raise RuntimeError("There is no tray-managed local agent process to stop.")
            process = self._process
            assert process is not None
            process.terminate()
            try:
                process.wait(timeout=10)
            except Exception:
                process.kill()
                process.wait(timeout=5)
            self._process = None
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
                can_restart=lifecycle in {LifecycleState.RUNNING, LifecycleState.STARTING, LifecycleState.STOPPED},
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


def build_control_bridge(settings: TraySettings) -> AgentControlBridge:
    if settings.control_mode == "spawn":
        return SpawnedProcessAgentBridge(settings)
    if settings.control_mode == "service":
        return WindowsServiceAgentBridge(settings)
    return MonitorAgentBridge()
