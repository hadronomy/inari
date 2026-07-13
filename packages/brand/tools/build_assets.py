"""Build platform icon assets and fetch the licensed web fonts."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "inari_brand" / "assets"
FONTS = ASSETS / "fonts"
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 192, 256, 512, 1024)


@dataclass(frozen=True)
class FontSource:
    filename: str
    url: str
    sha256: str


FONT_SOURCES = (
    FontSource(
        "instrument-sans-latin.woff2",
        "https://fonts.gstatic.com/s/instrumentsans/v4/pxicypc9vsFDm051Uf6KVwgkfoSbT2lBgGygpg.woff2",
        "19f8ec3200550a11859c7e52d7378354bf71bc6dd0441fdde79b9a660513f5e4",
    ),
    FontSource(
        "ibm-plex-mono-400-latin.woff2",
        "https://fonts.gstatic.com/s/ibmplexmono/v20/-F63fjptAgt5VM-kVkqdyU8n1i8q131nj-o.woff2",
        "c36f509c0a8f9f85f29cb44bc8701d8a9e0b14c499e77a884f789ead7093a7ac",
    ),
    FontSource(
        "ibm-plex-mono-500-latin.woff2",
        "https://fonts.gstatic.com/s/ibmplexmono/v20/-F6qfjptAgt5VM-kVkqdyU8n3twJwlBFgsAXHNk.woff2",
        "a76f53ca6612e7b3828eec2311098675b7f9849ae4169a8bcef6302aec02a6c0",
    ),
)


def _run(*arguments: str) -> None:
    subprocess.run(arguments, check=True)


def _fetch_font(source: FontSource) -> None:
    destination = FONTS / source.filename
    with urllib.request.urlopen(source.url) as response:  # noqa: S310 - pinned HTTPS source
        content = response.read()
    digest = hashlib.sha256(content).hexdigest()
    if digest != source.sha256:
        raise RuntimeError(f"unexpected digest for {source.filename}: {digest}")
    destination.write_bytes(content)


def _render_icons() -> None:
    source = ASSETS / "inari-app-icon.svg"
    for size in ICON_SIZES:
        _run(
            "magick",
            "-background",
            "none",
            str(source),
            "-resize",
            f"{size}x{size}",
            "-depth",
            "8",
            "-strip",
            "-define",
            "png:compression-level=9",
            str(ASSETS / f"inari-icon-{size}.png"),
        )

    _run(
        "magick",
        *(str(ASSETS / f"inari-icon-{size}.png") for size in (16, 24, 32, 48, 256)),
        str(ASSETS / "inari.ico"),
    )


def _render_macos_icon() -> None:
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        return
    with tempfile.TemporaryDirectory() as directory:
        iconset = Path(directory) / "inari.iconset"
        iconset.mkdir()
        mappings = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
            "icon_512x512@2x.png": 1024,
        }
        for filename, size in mappings.items():
            shutil.copy2(ASSETS / f"inari-icon-{size}.png", iconset / filename)
        _run(iconutil, "-c", "icns", str(iconset), "-o", str(ASSETS / "inari.icns"))


def main() -> None:
    FONTS.mkdir(parents=True, exist_ok=True)
    for source in FONT_SOURCES:
        _fetch_font(source)
    _render_icons()
    _render_macos_icon()


if __name__ == "__main__":
    main()
