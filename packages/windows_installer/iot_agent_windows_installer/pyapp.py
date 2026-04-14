from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Sequence

from .config import InstallerSettings, workspace_versions

CommandRunner = Callable[[Sequence[str], Path | None, dict[str, str] | None], subprocess.CompletedProcess[str]]


@dataclass(slots=True, frozen=True)
class LauncherSpec:
    launcher_id: str
    executable_name: str
    exec_spec: str
    package_name: str
    package_version: str
    dependency_file: Path
    is_gui: bool


def build_pyapp_launcher(
    settings: InstallerSettings,
    *,
    spec: LauncherSpec,
    output_dir: Path,
    runner: CommandRunner,
) -> Path:
    cargo_target_dir = settings.pyapp_build_dir / spec.launcher_id / "cargo-target"
    cargo_target_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "CARGO_TARGET_DIR": str(cargo_target_dir),
            "PYAPP_PROJECT_NAME": spec.package_name,
            "PYAPP_PROJECT_VERSION": spec.package_version,
            "PYAPP_PROJECT_DEPENDENCY_FILE": str(spec.dependency_file),
            "PYAPP_DISTRIBUTION_PATH": str(settings.paths.python_distribution_path),
            "PYAPP_UV_ENABLED": "1" if settings.pyapp.uv_enabled else "0",
            "PYAPP_EXEC_SPEC": spec.exec_spec,
            "PYAPP_IS_GUI": "1" if spec.is_gui else "0",
        }
    )
    if settings.pyapp.uv_version is not None:
        env["PYAPP_UV_VERSION"] = settings.pyapp.uv_version
    if settings.pyapp.uv_only_bootstrap:
        env["PYAPP_UV_ONLY_BOOTSTRAP"] = "1"

    runner([settings.tools.cargo, "build", "--release"], settings.paths.pyapp_source_dir, env)

    built_executable = cargo_target_dir / "release" / "pyapp.exe"
    if not built_executable.exists():
        raise RuntimeError(f"Expected PyApp launcher at {built_executable}, but it was not produced.")

    destination = output_dir / spec.executable_name
    shutil.copy2(built_executable, destination)
    return destination


def tray_launcher_spec(settings: InstallerSettings) -> LauncherSpec:
    versions = workspace_versions()
    return LauncherSpec(
        launcher_id="tray",
        executable_name=settings.tray.executable_name,
        exec_spec=settings.tray.exec_spec,
        package_name="iot-agent-tray",
        package_version=versions["iot-agent-tray"],
        dependency_file=settings.bundle_dir / "tray-requirements.txt",
        is_gui=True,
    )


def service_launcher_spec(settings: InstallerSettings) -> LauncherSpec:
    versions = workspace_versions()
    return LauncherSpec(
        launcher_id="service",
        executable_name=settings.service.executable_name,
        exec_spec=settings.service.exec_spec,
        package_name="iot-agent",
        package_version=versions["iot-agent"],
        dependency_file=settings.bundle_dir / "service-requirements.txt",
        is_gui=False,
    )


def write_launcher_requirements(settings: InstallerSettings) -> tuple[Path, Path]:
    settings.bundle_dir.mkdir(parents=True, exist_ok=True)
    tray_version = workspace_versions()["iot-agent-tray"]
    agent_version = workspace_versions()["iot-agent"]
    tray_requirements_path = settings.bundle_dir / "tray-requirements.txt"
    service_requirements_path = settings.bundle_dir / "service-requirements.txt"
    base_lines = [
        "--only-binary=:all:",
        "--find-links wheelhouse",
    ]
    tray_requirements_path.write_text(
        "\n".join([*base_lines, f"iot-agent-tray=={tray_version}", ""]),
        encoding="utf-8",
    )
    service_requirements_path.write_text(
        "\n".join([*base_lines, f"iot-agent=={agent_version}", ""]),
        encoding="utf-8",
    )
    return tray_requirements_path, service_requirements_path


def default_runner(
    command: Sequence[str],
    cwd: Path | None,
    env: dict[str, str] | None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=None if cwd is None else str(cwd),
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )


def pip_download_command(requirements_path: Path, destination: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--dest",
        str(destination),
        "--only-binary=:all:",
        "-r",
        str(requirements_path),
    ]
