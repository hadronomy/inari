from __future__ import annotations

from inari_brand import BrandAsset, read_asset
from PIL import Image, ImageDraw
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .models import TraySnapshot, TrayStatusLevel

ICON_SIZE = 64

_STATUS_COLORS: dict[TrayStatusLevel, tuple[int, int, int, int]] = {
    TrayStatusLevel.ONLINE: (47, 157, 105, 255),
    TrayStatusLevel.BUSY: (232, 166, 59, 255),
    TrayStatusLevel.DEGRADED: (226, 61, 40, 255),
    TrayStatusLevel.OFFLINE: (103, 107, 105, 255),
    TrayStatusLevel.STARTING: (77, 115, 201, 255),
    TrayStatusLevel.STOPPED: (174, 178, 175, 255),
}


def build_tray_icon(snapshot: TraySnapshot, *, size: int = ICON_SIZE) -> Image.Image:
    """Render the canonical tray mark with one semantic status overlay."""

    image = _render_svg(BrandAsset.TRAY_ICON, size=size)
    draw = ImageDraw.Draw(image)
    dot_size = max(12, size // 5)
    margin = max(2, size // 32)
    right = size - margin
    bottom = size - margin
    left = right - dot_size
    top = bottom - dot_size
    ring = max(2, size // 24)
    draw.ellipse(
        (left - ring, top - ring, right + ring, bottom + ring), fill=(15, 17, 16, 255)
    )
    draw.ellipse((left, top, right, bottom), fill=_STATUS_COLORS[snapshot.level])
    return image


def build_packaged_app_icon(*, size: int = ICON_SIZE) -> Image.Image:
    """Render the canonical application icon at an exact output size."""

    return _render_svg(BrandAsset.APP_ICON, size=size)


def _render_svg(asset: BrandAsset, *, size: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(read_asset(asset)))
    if not renderer.isValid():
        raise ValueError(f"invalid packaged brand asset: {asset.value}")

    surface = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    surface.fill(Qt.GlobalColor.transparent)
    painter = QPainter(surface)
    renderer.render(painter)
    painter.end()

    surface = surface.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes(
        "RGBA",
        (surface.width(), surface.height()),
        bytes(surface.constBits()),
        "raw",
        "RGBA",
        surface.bytesPerLine(),
        1,
    )
