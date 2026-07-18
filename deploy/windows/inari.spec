# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


SPEC_DIRECTORY = Path(SPECPATH)
WORKSPACE_ROOT = SPEC_DIRECTORY.parents[1]
EXECUTABLE_ICON = (
    WORKSPACE_ROOT
    / "target"
    / "release"
    / "windows"
    / "assets"
    / "InariDeviceCenter.ico"
)
PYTHON_PATHS = [
    str(WORKSPACE_ROOT / "packages" / "agent"),
]
WINDOWS_MODULES = [
    "pywintypes",
    "servicemanager",
    "win32crypt",
    "win32event",
    "win32file",
    "win32pipe",
    "win32print",
    "win32security",
    "win32service",
    "win32serviceutil",
]
INARI_LAZY_MODULES = [
    "inari.host_service.manager",
    "inari.host_service.models",
    "inari.local_api.app",
    "inari.printing.service",
]
INARI_MIGRATIONS = collect_submodules("inari.db.alembic.versions")


def analyze(
    entrypoint: str,
    *,
    datas: list[tuple[str, str]],
    hiddenimports: list[str],
) -> Analysis:
    return Analysis(
        [str(SPEC_DIRECTORY / entrypoint)],
        pathex=PYTHON_PATHS,
        binaries=[],
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
        optimize=0,
    )


agent_data = collect_data_files(
    "inari",
    includes=["db/alembic/script.py.mako"],
)
agent_service_analysis = analyze(
    "agent_service_entry.py",
    datas=agent_data,
    hiddenimports=INARI_LAZY_MODULES + INARI_MIGRATIONS + WINDOWS_MODULES,
)

agent_service_archive = PYZ(agent_service_analysis.pure)
agent_service = EXE(
    agent_service_archive,
    agent_service_analysis.scripts,
    [],
    exclude_binaries=True,
    name="InariAgentService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(EXECUTABLE_ICON),
)

bundle = COLLECT(
    agent_service,
    agent_service_analysis.binaries,
    agent_service_analysis.datas,
    strip=False,
    upx=False,
    name="InariAgentService",
)
