from __future__ import annotations

import argparse
import io
import shutil
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from inari_brand import BrandAsset, read_asset
from PIL import Image
from pydantic import BaseModel, ConfigDict

FOUNDATION = "http://schemas.microsoft.com/appx/manifest/foundation/windows10"
UAP = "http://schemas.microsoft.com/appx/manifest/uap/windows10"
DESKTOP6 = "http://schemas.microsoft.com/appx/manifest/desktop/windows10/6"
WINDOWS_EXECUTABLE_ICON_SIZES = (16, 24, 32, 48, 256)
WINDOWS_APP_LIST_ICON_SIZES = (
    16,
    20,
    24,
    30,
    32,
    36,
    40,
    48,
    60,
    64,
    72,
    80,
    96,
    256,
)
WINDOWS_APP_LIST_VARIANTS = (
    "",
    "_altform-unplated",
    "_altform-lightunplated",
)


class PackageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    msix_version: str
    identity_name: str
    publisher: str
    display_name: str
    publisher_display_name: str
    service_name: str
    architecture: Literal["x64"]
    minimum_windows_version: str


class PackageFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package: PackageMetadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build Inari's derived Windows assets."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    icon = commands.add_parser("icon", help="Render the executable ICO resource.")
    icon.add_argument("--output", type=Path, required=True)
    package = commands.add_parser("package", help="Prepare the MSIX package tree.")
    package.add_argument("--payload", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "icon":
        write_executable_icon(args.output)
        return
    metadata = prepare_package(payload=args.payload, output=args.output)
    print(encode_package_metadata(metadata))


def encode_package_metadata(metadata: PackageMetadata) -> str:
    return metadata.model_dump_json(ensure_ascii=True)


def prepare_package(*, payload: Path, output: Path) -> PackageMetadata:
    """Move a frozen application bundle into its final MSIX package tree."""
    source_dir = Path(__file__).resolve().parent
    metadata = _load_metadata(source_dir / "package.toml")
    if not payload.is_dir():
        raise FileNotFoundError(f"PyInstaller payload does not exist: {payload}")

    shutil.rmtree(output, ignore_errors=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload.replace(output)
    _write_manifest(source_dir / "AppxManifest.template.xml", output, metadata)
    _write_app_installer_data(source_dir, output)
    _write_assets(output / "Assets")
    return metadata


def _load_metadata(path: Path) -> PackageMetadata:
    with path.open("rb") as source:
        return PackageFile.model_validate(tomllib.load(source)).package


def _write_manifest(template: Path, output: Path, metadata: PackageMetadata) -> None:
    ET.register_namespace("", FOUNDATION)
    ET.register_namespace("uap", UAP)
    ET.register_namespace(
        "desktop", "http://schemas.microsoft.com/appx/manifest/desktop/windows10"
    )
    ET.register_namespace("desktop6", DESKTOP6)
    ET.register_namespace(
        "rescap",
        "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
    )
    tree = ET.parse(template)
    root = tree.getroot()
    identity = _required(root.find(f"{{{FOUNDATION}}}Identity"), "Identity")
    identity.attrib.update(
        {
            "Name": metadata.identity_name,
            "Publisher": metadata.publisher,
            "Version": metadata.msix_version,
            "ProcessorArchitecture": metadata.architecture,
        }
    )
    _required(
        root.find(f"{{{FOUNDATION}}}Properties/{{{FOUNDATION}}}DisplayName"),
        "DisplayName",
    ).text = metadata.display_name
    _required(
        root.find(f"{{{FOUNDATION}}}Properties/{{{FOUNDATION}}}PublisherDisplayName"),
        "PublisherDisplayName",
    ).text = metadata.publisher_display_name
    dependency = _required(
        root.find(f"{{{FOUNDATION}}}Dependencies/{{{FOUNDATION}}}TargetDeviceFamily"),
        "TargetDeviceFamily",
    )
    dependency.set("MinVersion", metadata.minimum_windows_version)
    application = _required(
        root.find(f"{{{FOUNDATION}}}Applications/{{{FOUNDATION}}}Application"),
        "Application",
    )
    visual = _required(application.find(f"{{{UAP}}}VisualElements"), "VisualElements")
    visual.set("DisplayName", metadata.display_name)
    service = _required(
        application.find(
            f"{{{FOUNDATION}}}Extensions/{{{DESKTOP6}}}Extension/{{{DESKTOP6}}}Service"
        ),
        "desktop6:Service",
    )
    service.set("Name", metadata.service_name)
    ET.indent(tree, space="  ")
    tree.write(output / "AppxManifest.xml", encoding="utf-8", xml_declaration=True)


def _write_app_installer_data(source_dir: Path, output: Path) -> None:
    destination = output / "Msix.AppInstaller.Data"
    destination.mkdir(parents=True)
    shutil.copy2(
        source_dir / "MsixAppInstallerData.xml",
        destination / "MSIXAppInstallerData.xml",
    )


def _write_assets(destination: Path) -> None:
    destination.mkdir(parents=True)
    square_sizes = {
        "StoreLogo.png": 50,
        "Square44x44Logo.png": 44,
        "Square150x150Logo.png": 150,
        "AppInstallerLogo.png": 96,
    }
    for name, size in square_sizes.items():
        _render_icon(width=size, height=size).save(destination / name)

    for size in WINDOWS_APP_LIST_ICON_SIZES:
        icon = _render_icon(width=size, height=size)
        for variant in WINDOWS_APP_LIST_VARIANTS:
            icon.save(destination / f"Square44x44Logo.targetsize-{size}{variant}.png")

    mark = _render_icon(width=150, height=150)
    wide = Image.new("RGBA", (310, 150), (242, 242, 239, 255))
    wide.alpha_composite(mark, ((wide.width - mark.width) // 2, 0))
    wide.save(destination / "Wide310x150Logo.png")


def write_executable_icon(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = _render_icon(width=256, height=256)
    source.save(
        destination,
        format="ICO",
        sizes=[(size, size) for size in WINDOWS_EXECUTABLE_ICON_SIZES],
    )


def _render_icon(*, width: int, height: int) -> Image.Image:
    with Image.open(io.BytesIO(read_asset(BrandAsset.APP_ICON_1024))) as source:
        icon = source.convert("RGBA").resize(
            (width, height),
            resample=Image.Resampling.LANCZOS,
        )
    alpha = icon.getchannel("A").point(lambda value: 0 if value <= 1 else value)
    icon.putalpha(alpha)
    return icon


def _required(element: ET.Element | None, name: str) -> ET.Element:
    if element is None:
        raise ValueError(f"MSIX manifest template is missing {name}.")
    return element


if __name__ == "__main__":
    main()
