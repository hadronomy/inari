from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
import tomllib

from packaging.version import Version
from pydantic import BaseModel, Field, field_validator

from iot_agent.version import API_VERSION, SERVICE_NAME
from iot_agent.windows_service import (
    WINDOWS_SERVICE_DESCRIPTION,
    WINDOWS_SERVICE_DISPLAY_NAME,
    WINDOWS_SERVICE_NAME,
)

Architecture = Literal["x64"]
ServiceStartAccount = Literal["localService", "localSystem"]
ServiceStartupType = Literal["auto", "manual", "disabled"]


class PackageIdentitySettings(BaseModel):
    package_id: str
    publisher: str
    publisher_display_name: str
    display_name: str = SERVICE_NAME
    description: str = "Secure local IoT agent and tray companion."
    version: str | None = None
    architecture: Architecture = "x64"
    min_windows_version: str = "10.0.19041.0"
    max_windows_version_tested: str = "10.0.26100.0"


class PathSettings(BaseModel):
    build_dir: Path = Path("./build/windows")
    dist_dir: Path = Path("./dist/windows")
    pyapp_source_dir: Path
    python_distribution_path: Path
    certificate_path: Path | None = None


class ToolSettings(BaseModel):
    uv: str = "uv"
    cargo: str = "cargo"
    makepri: str = "makepri"
    makeappx: str = "makeappx"
    signtool: str = "signtool"


class PyAppSettings(BaseModel):
    uv_enabled: bool = True
    uv_version: str | None = None
    uv_only_bootstrap: bool = False
    vendor_transitive_wheels: bool = True


class TraySettings(BaseModel):
    executable_name: str = "IoT Agent Tray.exe"
    exec_spec: str = "iot_agent_tray.main:main"
    app_id: str = "IoTAgentTray"
    startup_task_enabled: bool = False
    startup_task_id: str = "IoTAgentTrayStartup"
    startup_task_display_name: str = "IoT Agent Tray"


class ServiceSettings(BaseModel):
    executable_name: str = "IoT Agent Service.exe"
    exec_spec: str = "iot_agent.windows_service:main"
    app_id: str = "IoTAgentServiceHost"
    service_enabled: bool = True
    service_name: str = WINDOWS_SERVICE_NAME
    service_display_name: str = WINDOWS_SERVICE_DISPLAY_NAME
    service_description: str = WINDOWS_SERVICE_DESCRIPTION
    start_account: ServiceStartAccount = "localService"
    startup_type: ServiceStartupType = "auto"


class SigningSettings(BaseModel):
    enabled: bool = False
    certificate_password_env_var: str = "MSIX_CERT_PASSWORD"
    timestamp_url: str | None = None


class InstallerSettings(BaseModel):
    config_version: int = 1
    identity: PackageIdentitySettings
    paths: PathSettings
    tools: ToolSettings = Field(default_factory=ToolSettings)
    pyapp: PyAppSettings = Field(default_factory=PyAppSettings)
    tray: TraySettings = Field(default_factory=TraySettings)
    service: ServiceSettings = Field(default_factory=ServiceSettings)
    signing: SigningSettings = Field(default_factory=SigningSettings)

    @field_validator("config_version")
    @classmethod
    def validate_config_version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("Only config_version = 1 is supported.")
        return value

    @property
    def msix_version(self) -> str:
        return self.identity.version or msix_version_from_pep440(API_VERSION)

    @property
    def wheelhouse_dir(self) -> Path:
        return self.bundle_dir / "wheelhouse"

    @property
    def bundle_dir(self) -> Path:
        return self.paths.build_dir / "bundle"

    @property
    def pyapp_build_dir(self) -> Path:
        return self.paths.build_dir / "pyapp"

    @property
    def msix_layout_dir(self) -> Path:
        return self.paths.build_dir / "msix"

    @property
    def assets_dir(self) -> Path:
        return self.msix_layout_dir / "Assets"

    @property
    def pri_config_path(self) -> Path:
        return self.msix_layout_dir / "priconfig.xml"

    @property
    def manifest_path(self) -> Path:
        return self.msix_layout_dir / "AppxManifest.xml"

    @property
    def msix_output_path(self) -> Path:
        sanitized = self.identity.display_name.replace(" ", "")
        filename = f"{sanitized}_{self.msix_version}_{self.identity.architecture}.msix"
        return self.paths.dist_dir / filename


def default_installer_config_path() -> Path:
    return workspace_root() / "packaging" / "windows" / "iot-agent-windows.toml"


def load_installer_settings(config_path: Path | None = None) -> InstallerSettings:
    resolved_config_path = config_path or default_installer_config_path()
    with resolved_config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    settings = InstallerSettings.model_validate(raw)
    return _resolve_relative_paths(settings, base_dir=resolved_config_path.parent)


def workspace_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        pyproject_path = parent / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8")
            if "[tool.uv.workspace]" in content:
                return parent
    raise RuntimeError("Unable to locate the workspace root.")


def msix_version_from_pep440(version_text: str) -> str:
    parsed = Version(version_text)
    release = list(parsed.release[:3])
    while len(release) < 3:
        release.append(0)

    build = 0
    if parsed.pre is not None:
        build = int(parsed.pre[1])
    elif parsed.post is not None:
        build = int(parsed.post)
    elif parsed.dev is not None:
        build = int(parsed.dev)

    return ".".join(str(part) for part in [*release, build])


def _resolve_relative_paths(settings: InstallerSettings, *, base_dir: Path) -> InstallerSettings:
    path_updates = {
        "build_dir": _resolve_path(base_dir, settings.paths.build_dir),
        "dist_dir": _resolve_path(base_dir, settings.paths.dist_dir),
        "pyapp_source_dir": _resolve_path(base_dir, settings.paths.pyapp_source_dir),
        "python_distribution_path": _resolve_path(base_dir, settings.paths.python_distribution_path),
        "certificate_path": (
            _resolve_path(base_dir, settings.paths.certificate_path)
            if settings.paths.certificate_path is not None
            else None
        ),
    }
    return settings.model_copy(update={"paths": settings.paths.model_copy(update=path_updates)})


def _resolve_path(base_dir: Path, value: Path) -> Path:
    if value.is_absolute():
        return value
    return (base_dir / value).resolve()


@lru_cache(maxsize=1)
def workspace_versions() -> dict[str, str]:
    root = workspace_root()
    packages = {
        "iot-agent": root / "packages" / "agent" / "pyproject.toml",
        "iot-agent-tray": root / "packages" / "agent_tray" / "pyproject.toml",
    }
    versions: dict[str, str] = {}
    for name, path in packages.items():
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        versions[name] = str(data["project"]["version"])
    return versions
