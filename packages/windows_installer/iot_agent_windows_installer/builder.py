from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

from .assets import AssetPaths, generate_msix_assets
from .config import InstallerSettings, workspace_root
from .msix import write_msix_manifest, write_priconfig
from .pyapp import (
    LauncherSpec,
    build_pyapp_launcher,
    default_runner,
    pip_download_command,
    service_launcher_spec,
    tray_launcher_spec,
    write_launcher_requirements,
)


@dataclass(slots=True, frozen=True)
class StageResult:
    tray_executable: Path
    service_executable: Path | None
    manifest_path: Path
    asset_paths: AssetPaths


class WindowsInstallerBuilder:
    def __init__(self, settings: InstallerSettings) -> None:
        self.settings = settings

    def stage(self) -> StageResult:
        self._prepare_directories()
        self._build_workspace_wheels()
        self._vendor_transitive_dependencies()
        write_launcher_requirements(self.settings)

        tray_executable = build_pyapp_launcher(
            self.settings,
            spec=tray_launcher_spec(self.settings),
            output_dir=self.settings.msix_layout_dir,
            runner=default_runner,
        )
        service_executable: Path | None = None
        if self.settings.service.service_enabled:
            service_executable = build_pyapp_launcher(
                self.settings,
                spec=service_launcher_spec(self.settings),
                output_dir=self.settings.msix_layout_dir,
                runner=default_runner,
            )

        asset_paths = generate_msix_assets(self.settings.assets_dir)
        write_priconfig(self.settings.pri_config_path)
        write_msix_manifest(self.settings, assets=asset_paths, output_path=self.settings.manifest_path)

        return StageResult(
            tray_executable=tray_executable,
            service_executable=service_executable,
            manifest_path=self.settings.manifest_path,
            asset_paths=asset_paths,
        )

    def package(self, *, sign: bool | None = None) -> Path:
        self.stage()
        self.settings.paths.dist_dir.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                self.settings.tools.makepri,
                "new",
                "/pr",
                str(self.settings.msix_layout_dir),
                "/cf",
                str(self.settings.pri_config_path),
            ]
        )
        self._run(
            [
                self.settings.tools.makeappx,
                "pack",
                "/d",
                str(self.settings.msix_layout_dir),
                "/p",
                str(self.settings.msix_output_path),
            ]
        )
        if sign if sign is not None else self.settings.signing.enabled:
            self._sign_msix(self.settings.msix_output_path)
        return self.settings.msix_output_path

    def _prepare_directories(self) -> None:
        for path in [
            self.settings.paths.build_dir,
            self.settings.paths.dist_dir,
            self.settings.wheelhouse_dir,
            self.settings.bundle_dir,
            self.settings.pyapp_build_dir,
            self.settings.msix_layout_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

    def _build_workspace_wheels(self) -> None:
        root = workspace_root()
        for package_name in ("iot-agent", "iot-agent-tray"):
            self._run(
                [
                    self.settings.tools.uv,
                    "build",
                    "--package",
                    package_name,
                    "--wheel",
                    "--out-dir",
                    str(self.settings.wheelhouse_dir),
                ],
                cwd=root,
            )

    def _vendor_transitive_dependencies(self) -> None:
        if not self.settings.pyapp.vendor_transitive_wheels:
            return
        export_path = self.settings.bundle_dir / "third-party-requirements.txt"
        root = workspace_root()
        self._run(
            [
                self.settings.tools.uv,
                "export",
                "--package",
                "iot-agent-tray",
                "--no-dev",
                "--no-editable",
                "--no-header",
                "--no-hashes",
                "--no-emit-package",
                "iot-agent-tray",
                "--no-emit-package",
                "iot-agent",
                "--output-file",
                str(export_path),
            ],
            cwd=root,
        )
        requirements_text = export_path.read_text(encoding="utf-8").strip()
        if not requirements_text:
            return
        self._run(pip_download_command(export_path, self.settings.wheelhouse_dir))

    def _sign_msix(self, package_path: Path) -> None:
        certificate_path = self.settings.paths.certificate_path
        if certificate_path is None:
            raise RuntimeError("signing.enabled is true but no certificate_path is configured.")
        password = os.environ.get(self.settings.signing.certificate_password_env_var)
        if not password:
            raise RuntimeError(
                f"Environment variable {self.settings.signing.certificate_password_env_var!r} is required for signing."
            )
        command = [
            self.settings.tools.signtool,
            "sign",
            "/fd",
            "SHA256",
            "/f",
            str(certificate_path),
            "/p",
            password,
        ]
        if self.settings.signing.timestamp_url:
            command.extend(["/tr", self.settings.signing.timestamp_url, "/td", "SHA256"])
        command.append(str(package_path))
        self._run(command)

    @staticmethod
    def _run(command: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return default_runner(command, cwd, None)
