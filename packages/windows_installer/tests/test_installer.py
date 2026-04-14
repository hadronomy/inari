from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from typer.testing import CliRunner

from iot_agent_windows_installer.assets import generate_msix_assets
from iot_agent_windows_installer.cli import app
from iot_agent_windows_installer.config import InstallerSettings, msix_version_from_pep440
from iot_agent_windows_installer.msix import write_msix_manifest
from iot_agent_windows_installer.pyapp import LauncherSpec, build_pyapp_launcher, write_launcher_requirements


def test_msix_version_from_pep440_maps_prerelease_to_build_segment() -> None:
    assert msix_version_from_pep440("1.17.0a1") == "1.17.0.1"
    assert msix_version_from_pep440("2.3.4") == "2.3.4.0"


def test_generate_msix_assets_creates_transparent_branding_images(tmp_path) -> None:
    assets = generate_msix_assets(tmp_path)

    for path in (assets.square_44, assets.square_150, assets.store_logo):
        assert path.exists()


def test_write_msix_manifest_includes_startup_task_and_service(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        tray_overrides={"startup_task_enabled": True},
        signing_overrides={"enabled": False},
    )
    assets = generate_msix_assets(settings.assets_dir)

    write_msix_manifest(settings, assets=assets, output_path=settings.manifest_path)

    document = ET.parse(settings.manifest_path)
    xml = ET.tostring(document.getroot(), encoding="unicode")

    assert "windows.startupTask" in xml
    assert settings.tray.startup_task_id in xml
    assert "windows.service" in xml
    assert settings.service.service_name in xml


def test_write_launcher_requirements_points_to_local_wheelhouse(tmp_path) -> None:
    settings = _settings(tmp_path)

    tray_requirements, service_requirements = write_launcher_requirements(settings)

    assert "--find-links wheelhouse" in tray_requirements.read_text(encoding="utf-8")
    assert "iot-agent-tray==" in tray_requirements.read_text(encoding="utf-8")
    assert "iot-agent==" in service_requirements.read_text(encoding="utf-8")


def test_build_pyapp_launcher_builds_into_named_executable(tmp_path) -> None:
    settings = _settings(tmp_path)
    dependency_file = settings.bundle_dir / "tray-requirements.txt"
    dependency_file.parent.mkdir(parents=True, exist_ok=True)
    dependency_file.write_text("iot-agent-tray==0.3.0a1\n", encoding="utf-8")
    output_dir = tmp_path / "dist"

    def runner(command, cwd, env):
        built = Path(env["CARGO_TARGET_DIR"]) / "release" / "pyapp.exe"
        built.parent.mkdir(parents=True, exist_ok=True)
        built.write_bytes(b"exe")
        return type("Completed", (), {"stdout": "", "stderr": ""})()

    executable = build_pyapp_launcher(
        settings,
        spec=LauncherSpec(
            launcher_id="tray",
            executable_name="IoT Agent Tray.exe",
            exec_spec="iot_agent_tray.main:main",
            package_name="iot-agent-tray",
            package_version="0.3.0a1",
            dependency_file=dependency_file,
            is_gui=True,
        ),
        output_dir=output_dir,
        runner=runner,
    )

    assert executable == output_dir / "IoT Agent Tray.exe"
    assert executable.exists()


def test_init_config_copies_example(tmp_path) -> None:
    destination = tmp_path / "iot-agent-windows.toml"

    result = CliRunner().invoke(app, ["init-config", str(destination)])

    assert result.exit_code == 0, result.output
    assert destination.exists()
    assert "package_id" in destination.read_text(encoding="utf-8")


def _settings(
    tmp_path: Path,
    *,
    tray_overrides: dict[str, object] | None = None,
    signing_overrides: dict[str, object] | None = None,
) -> InstallerSettings:
    return InstallerSettings.model_validate(
        {
            "config_version": 1,
            "identity": {
                "package_id": "com.example.iotagent",
                "publisher": "CN=Example Publisher",
                "publisher_display_name": "Example Publisher",
            },
            "paths": {
                "build_dir": str(tmp_path / "build"),
                "dist_dir": str(tmp_path / "dist"),
                "pyapp_source_dir": str(tmp_path / "vendor" / "pyapp"),
                "python_distribution_path": str(tmp_path / "vendor" / "python.zip"),
                "certificate_path": str(tmp_path / "certs" / "test.pfx"),
            },
            "tray": tray_overrides or {},
            "signing": signing_overrides or {},
        }
    )
