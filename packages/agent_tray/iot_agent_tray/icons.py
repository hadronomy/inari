from __future__ import annotations

from PIL import Image, ImageDraw

from .models import TraySnapshot, TrayStatusLevel

ICON_SIZE = 64


def build_tray_icon(snapshot: TraySnapshot, *, size: int = ICON_SIZE) -> Image.Image:
    background, panel, accent = _palette(snapshot.level)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    outer_margin = max(4, size // 10)
    panel_top = outer_margin + max(2, size // 16)
    radius = max(10, size // 5)
    draw.rounded_rectangle(
        (outer_margin, outer_margin, size - outer_margin, size - outer_margin),
        radius=radius,
        fill=background,
    )
    draw.rounded_rectangle(
        (outer_margin + 2, outer_margin + 2, size - outer_margin - 2, panel_top + size // 3),
        radius=max(8, radius - 3),
        fill=panel,
    )

    paper_left = size * 0.3
    paper_top = size * 0.18
    paper_right = size * 0.7
    paper_bottom = size * 0.46
    draw.rounded_rectangle(
        (paper_left, paper_top, paper_right, paper_bottom),
        radius=max(4, size // 12),
        fill=(255, 255, 255, 240),
    )

    body_left = size * 0.2
    body_top = size * 0.35
    body_right = size * 0.8
    body_bottom = size * 0.74
    draw.rounded_rectangle(
        (body_left, body_top, body_right, body_bottom),
        radius=max(6, size // 10),
        fill=(244, 247, 250, 245),
    )

    slot_y = int(size * 0.52)
    draw.line(
        (int(size * 0.34), slot_y, int(size * 0.66), slot_y),
        fill=(94, 110, 126, 255),
        width=max(2, size // 18),
    )
    draw.line(
        (int(size * 0.34), int(size * 0.60), int(size * 0.58), int(size * 0.60)),
        fill=(163, 174, 184, 255),
        width=max(2, size // 24),
    )

    dot_size = max(10, size // 5)
    dot_margin = max(6, size // 12)
    draw.ellipse(
        (
            size - outer_margin - dot_size - dot_margin,
            size - outer_margin - dot_size - dot_margin,
            size - outer_margin - dot_margin,
            size - outer_margin - dot_margin,
        ),
        fill=accent,
    )

    return image


def _palette(level: TrayStatusLevel) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]:
    palettes = {
        TrayStatusLevel.ONLINE: ((21, 108, 74, 255), (49, 145, 104, 255), (145, 255, 196, 255)),
        TrayStatusLevel.BUSY: ((16, 84, 147, 255), (39, 120, 201, 255), (157, 214, 255, 255)),
        TrayStatusLevel.DEGRADED: ((171, 98, 20, 255), (214, 138, 40, 255), (255, 216, 145, 255)),
        TrayStatusLevel.OFFLINE: ((130, 38, 53, 255), (181, 57, 79, 255), (255, 167, 187, 255)),
        TrayStatusLevel.STARTING: ((82, 63, 168, 255), (116, 92, 219, 255), (202, 194, 255, 255)),
        TrayStatusLevel.STOPPED: ((77, 83, 95, 255), (111, 119, 136, 255), (202, 209, 222, 255)),
    }
    return palettes[level]
