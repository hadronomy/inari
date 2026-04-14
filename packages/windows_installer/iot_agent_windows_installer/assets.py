from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from iot_agent_tray.icons import build_packaged_app_icon


@dataclass(slots=True, frozen=True)
class AssetPaths:
    square_44: Path
    square_150: Path
    store_logo: Path


def generate_msix_assets(output_dir: Path) -> AssetPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_map = {
        "Square44x44Logo.png": 44,
        "Square150x150Logo.png": 150,
        "StoreLogo.png": 50,
    }
    for filename, size in asset_map.items():
        icon = build_packaged_app_icon(size=256)
        rendered = _fit_icon_to_canvas(icon, size)
        rendered.save(output_dir / filename)
    return AssetPaths(
        square_44=output_dir / "Square44x44Logo.png",
        square_150=output_dir / "Square150x150Logo.png",
        store_logo=output_dir / "StoreLogo.png",
    )


def _fit_icon_to_canvas(source: Image.Image, size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset = max(4, size // 12)
    target_size = size - (inset * 2)
    resized = source.resize((target_size, target_size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(resized, dest=(inset, inset))
    return canvas
