from __future__ import annotations

import xml.etree.ElementTree as ET

from PIL import Image
from PIL.IcoImagePlugin import IcoImageFile

from deploy.windows.build import (
    DESKTOP6,
    FOUNDATION,
    UAP,
    WINDOWS_ICON_SIZES,
    encode_package_metadata,
    prepare_package,
    write_executable_icon,
)


def test_package_tree_claims_payload_and_uses_canonical_assets(tmp_path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "InariDeviceCenter.exe").write_bytes(b"device-center")
    (payload / "InariAgentService.exe").write_bytes(b"agent-service")
    output = tmp_path / "package"

    metadata = prepare_package(payload=payload, output=output)

    assert not payload.exists()
    manifest = ET.parse(output / "AppxManifest.xml").getroot()
    identity = manifest.find(f"{{{FOUNDATION}}}Identity")
    assert identity is not None
    assert identity.attrib == {
        "Name": metadata.identity_name,
        "Publisher": metadata.publisher,
        "Version": metadata.msix_version,
        "ProcessorArchitecture": "x64",
    }
    properties = manifest.find(f"{{{FOUNDATION}}}Properties")
    assert properties is not None
    publisher_name = properties.find(f"{{{FOUNDATION}}}PublisherDisplayName")
    assert publisher_name is not None
    assert publisher_name.text == "Pablo Hernández Jiménez · Inari"
    application = manifest.find(
        f"{{{FOUNDATION}}}Applications/{{{FOUNDATION}}}Application"
    )
    assert application is not None
    visual = application.find(f"{{{UAP}}}VisualElements")
    assert visual is not None
    assert visual.attrib["DisplayName"] == "Inari Device Center"
    service = application.find(
        f"{{{FOUNDATION}}}Extensions/{{{DESKTOP6}}}Extension/{{{DESKTOP6}}}Service"
    )
    assert service is not None
    assert service.attrib == {
        "Name": "InariAgent",
        "StartupType": "auto",
        "StartAccount": "localService",
    }

    assert (output / "Msix.AppInstaller.Data" / "MSIXAppInstallerData.xml").is_file()
    expected_sizes = {
        "StoreLogo.png": (50, 50),
        "Square44x44Logo.png": (44, 44),
        "Square150x150Logo.png": (150, 150),
        "Wide310x150Logo.png": (310, 150),
        "AppInstallerLogo.png": (96, 96),
    }
    for filename, size in expected_sizes.items():
        with Image.open(output / "Assets" / filename) as asset:
            assert asset.size == size


def test_executable_icon_contains_each_required_windows_size(tmp_path) -> None:
    icon_path = tmp_path / "InariDeviceCenter.ico"

    write_executable_icon(icon_path)

    with Image.open(icon_path) as icon:
        assert isinstance(icon, IcoImageFile)
        assert icon.ico.sizes() == {(size, size) for size in WINDOWS_ICON_SIZES}


def test_package_metadata_is_safe_across_windows_console_encodings(tmp_path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    metadata = prepare_package(payload=payload, output=tmp_path / "package")

    encoded = encode_package_metadata(metadata)

    assert encoded.isascii()
    assert '"publisher":"CN=Pablo Hern\\u00e1ndez Jim\\u00e9nez"' in encoded
