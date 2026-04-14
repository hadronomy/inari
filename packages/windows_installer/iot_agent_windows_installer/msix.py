from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .assets import AssetPaths
from .config import InstallerSettings

NAMESPACES = {
    "": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
    "desktop6": "http://schemas.microsoft.com/appx/manifest/desktop/windows10/6",
    "uap10": "http://schemas.microsoft.com/appx/manifest/uap/windows10/10",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}

for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


def write_msix_manifest(
    settings: InstallerSettings,
    *,
    assets: AssetPaths,
    output_path: Path,
) -> None:
    root = ET.Element(
        _tag("Package"),
        {
            "IgnorableNamespaces": "uap desktop desktop6 uap10 rescap",
        },
    )

    ET.SubElement(
        root,
        _tag("Identity"),
        {
            "Name": settings.identity.package_id,
            "Publisher": settings.identity.publisher,
            "Version": settings.msix_version,
            "ProcessorArchitecture": settings.identity.architecture,
        },
    )

    properties = ET.SubElement(root, _tag("Properties"))
    ET.SubElement(properties, _tag("DisplayName")).text = settings.identity.display_name
    ET.SubElement(properties, _tag("PublisherDisplayName")).text = settings.identity.publisher_display_name
    ET.SubElement(properties, _tag("Description")).text = settings.identity.description
    ET.SubElement(properties, _tag("Logo")).text = _relative_path(output_path.parent, assets.store_logo)

    resources = ET.SubElement(root, _tag("Resources"))
    ET.SubElement(resources, _tag("Resource"), {"Language": "en-us"})

    dependencies = ET.SubElement(root, _tag("Dependencies"))
    ET.SubElement(
        dependencies,
        _tag("TargetDeviceFamily"),
        {
            "Name": "Windows.Desktop",
            "MinVersion": settings.identity.min_windows_version,
            "MaxVersionTested": settings.identity.max_windows_version_tested,
        },
    )

    capabilities = ET.SubElement(root, _tag("Capabilities"))
    ET.SubElement(capabilities, _tag("rescap:Capability"), {"Name": "runFullTrust"})
    if settings.service.service_enabled:
        ET.SubElement(capabilities, _tag("rescap:Capability"), {"Name": "packagedServices"})
        if settings.service.start_account == "localSystem":
            ET.SubElement(capabilities, _tag("rescap:Capability"), {"Name": "localSystemServices"})

    applications = ET.SubElement(root, _tag("Applications"))
    tray_application = ET.SubElement(
        applications,
        _tag("Application"),
        {
            "Id": settings.tray.app_id,
            "Executable": settings.tray.executable_name,
            "EntryPoint": "Windows.FullTrustApplication",
            _tag("uap10:RuntimeBehavior"): "packagedClassicApp",
            _tag("uap10:TrustLevel"): "mediumIL",
        },
    )
    _add_visual_elements(
        tray_application,
        settings=settings,
        assets=assets,
        app_list_entry=None,
        description=settings.identity.description,
        display_name=settings.identity.display_name,
    )
    tray_extensions = ET.SubElement(tray_application, _tag("Extensions"))
    if settings.tray.startup_task_enabled:
        startup_extension = ET.SubElement(
            tray_extensions,
            _tag("desktop:Extension"),
            {
                "Category": "windows.startupTask",
                "Executable": settings.tray.executable_name,
                "EntryPoint": "Windows.FullTrustApplication",
            },
        )
        ET.SubElement(
            startup_extension,
            _tag("desktop:StartupTask"),
            {
                "TaskId": settings.tray.startup_task_id,
                "Enabled": "true",
                "DisplayName": settings.tray.startup_task_display_name,
            },
        )

    if settings.service.service_enabled:
        service_application = ET.SubElement(
            applications,
            _tag("Application"),
            {
                "Id": settings.service.app_id,
                "Executable": settings.service.executable_name,
                "EntryPoint": "Windows.FullTrustApplication",
                _tag("uap10:RuntimeBehavior"): "packagedClassicApp",
                _tag("uap10:TrustLevel"): "mediumIL",
            },
        )
        _add_visual_elements(
            service_application,
            settings=settings,
            assets=assets,
            app_list_entry="none",
            description=settings.service.service_description,
            display_name=settings.service.service_display_name,
        )
        service_extensions = ET.SubElement(service_application, _tag("Extensions"))
        service_extension = ET.SubElement(
            service_extensions,
            _tag("desktop6:Extension"),
            {
                "Category": "windows.service",
                "Executable": settings.service.executable_name,
                "EntryPoint": "Windows.FullTrustApplication",
            },
        )
        ET.SubElement(
            service_extension,
            _tag("desktop6:Service"),
            {
                "Name": settings.service.service_name,
                "StartupType": settings.service.startup_type,
                "StartAccount": settings.service.start_account,
            },
        )

    ET.indent(root)
    output_path.write_text('<?xml version="1.0" encoding="utf-8"?>\n', encoding="utf-8")
    output_path.write_text(
        output_path.read_text(encoding="utf-8") + ET.tostring(root, encoding="unicode"),
        encoding="utf-8",
    )


def write_priconfig(output_path: Path) -> None:
    output_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<resources targetOsVersion="10.0.0" majorVersion="1">
  <index root="." startIndexAt="\">
    <default>
      <qualifier name="Language" value="en-US" />
    </default>
  </index>
</resources>
""",
        encoding="utf-8",
    )


def _add_visual_elements(
    application: ET.Element,
    *,
    settings: InstallerSettings,
    assets: AssetPaths,
    app_list_entry: str | None,
    description: str,
    display_name: str,
) -> None:
    attributes = {
        "DisplayName": display_name,
        "Description": description,
        "BackgroundColor": "transparent",
        "Square44x44Logo": _relative_path(settings.msix_layout_dir, assets.square_44),
        "Square150x150Logo": _relative_path(settings.msix_layout_dir, assets.square_150),
    }
    if app_list_entry is not None:
        attributes["AppListEntry"] = app_list_entry
    ET.SubElement(application, _tag("uap:VisualElements"), attributes)


def _relative_path(base_dir: Path, value: Path) -> str:
    return str(value.relative_to(base_dir)).replace("/", "\\")


def _tag(name: str) -> str:
    if ":" not in name:
        return f"{{{NAMESPACES['']}}}{name}"
    prefix, local = name.split(":", 1)
    return f"{{{NAMESPACES[prefix]}}}{local}"
