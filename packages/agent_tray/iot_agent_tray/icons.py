from __future__ import annotations

from PIL import Image, ImageDraw

from .models import TraySnapshot, TrayStatusLevel

ICON_SIZE = 64


def build_tray_icon(snapshot: TraySnapshot, *, size: int = ICON_SIZE) -> Image.Image:
    glyph_fill, glyph_detail, glyph_shadow, accent = _palette(snapshot.level)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_gateway_glyph(
        draw,
        size=size,
        glyph_fill=glyph_fill,
        glyph_detail=glyph_detail,
        glyph_shadow=glyph_shadow,
    )

    dot_size = max(11, size // 5)
    dot_margin = max(4, size // 14)
    draw.ellipse(
        (
            size - dot_size - dot_margin,
            size - dot_size - dot_margin,
            size - dot_margin,
            size - dot_margin,
        ),
        fill=accent,
    )

    return image

def build_packaged_app_icon(*, size: int = ICON_SIZE) -> Image.Image:
    glyph_fill = (246, 248, 250, 238)
    glyph_detail = (104, 116, 128, 232)
    glyph_shadow = (0, 0, 0, 58)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    _draw_gateway_glyph(
        draw,
        size=size,
        glyph_fill=glyph_fill,
        glyph_detail=glyph_detail,
        glyph_shadow=glyph_shadow,
    )
    return image


def _draw_gateway_glyph(
    draw: ImageDraw.ImageDraw,
    *,
    size: int,
    glyph_fill: tuple[int, int, int, int],
    glyph_detail: tuple[int, int, int, int],
    glyph_shadow: tuple[int, int, int, int],
) -> None:
    center_left = size * 0.32
    center_top = size * 0.30
    center_right = size * 0.68
    center_bottom = size * 0.66
    center_x = size * 0.50
    center_y = size * 0.48
    shadow_offset = max(1, size // 48)
    stroke_width = max(3, size // 16)
    node_radius = max(5, size // 12)

    top_left_node = (size * 0.24, size * 0.22)
    top_right_node = (size * 0.76, size * 0.22)
    lower_left_node = (size * 0.18, size * 0.66)

    _draw_connector(
        draw,
        start=top_left_node,
        end=(center_left + size * 0.03, center_top + size * 0.06),
        fill=glyph_shadow,
        width=stroke_width,
        offset=shadow_offset,
    )
    _draw_connector(
        draw,
        start=top_right_node,
        end=(center_right - size * 0.03, center_top + size * 0.06),
        fill=glyph_shadow,
        width=stroke_width,
        offset=shadow_offset,
    )
    _draw_connector(
        draw,
        start=lower_left_node,
        end=(center_left + size * 0.04, center_bottom - size * 0.05),
        fill=glyph_shadow,
        width=stroke_width,
        offset=shadow_offset,
    )
    _draw_connector(
        draw,
        start=top_left_node,
        end=(center_left + size * 0.03, center_top + size * 0.06),
        fill=glyph_detail,
        width=stroke_width,
        offset=0,
    )
    _draw_connector(
        draw,
        start=top_right_node,
        end=(center_right - size * 0.03, center_top + size * 0.06),
        fill=glyph_detail,
        width=stroke_width,
        offset=0,
    )
    _draw_connector(
        draw,
        start=lower_left_node,
        end=(center_left + size * 0.04, center_bottom - size * 0.05),
        fill=glyph_detail,
        width=stroke_width,
        offset=0,
    )

    _draw_shadowed_rounded_rectangle(
        draw,
        (center_left, center_top, center_right, center_bottom),
        radius=max(8, size // 9),
        fill=glyph_fill,
        shadow=glyph_shadow,
        shadow_offset=shadow_offset,
    )

    inner_radius = max(3, size // 18)
    draw.rounded_rectangle(
        (
            center_x - size * 0.08,
            center_y - size * 0.08,
            center_x + size * 0.08,
            center_y + size * 0.08,
        ),
        radius=inner_radius,
        fill=glyph_detail,
    )

    _draw_shadowed_node(
        draw,
        center=top_left_node,
        radius=node_radius,
        fill=glyph_fill,
        shadow=glyph_shadow,
        shadow_offset=shadow_offset,
    )
    _draw_shadowed_node(
        draw,
        center=top_right_node,
        radius=node_radius,
        fill=glyph_fill,
        shadow=glyph_shadow,
        shadow_offset=shadow_offset,
    )
    _draw_shadowed_node(
        draw,
        center=lower_left_node,
        radius=node_radius,
        fill=glyph_fill,
        shadow=glyph_shadow,
        shadow_offset=shadow_offset,
    )


def _draw_shadowed_rounded_rectangle(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    *,
    radius: int,
    fill: tuple[int, int, int, int],
    shadow: tuple[int, int, int, int],
    shadow_offset: int,
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(
        (
            left + shadow_offset,
            top + shadow_offset,
            right + shadow_offset,
            bottom + shadow_offset,
        ),
        radius=radius,
        fill=shadow,
    )
    draw.rounded_rectangle(bounds, radius=radius, fill=fill)


def _draw_shadowed_node(
    draw: ImageDraw.ImageDraw,
    *,
    center: tuple[float, float],
    radius: int,
    fill: tuple[int, int, int, int],
    shadow: tuple[int, int, int, int],
    shadow_offset: int,
) -> None:
    x, y = center
    shadow_bounds = (
        x - radius + shadow_offset,
        y - radius + shadow_offset,
        x + radius + shadow_offset,
        y + radius + shadow_offset,
    )
    bounds = (x - radius, y - radius, x + radius, y + radius)
    draw.ellipse(shadow_bounds, fill=shadow)
    draw.ellipse(bounds, fill=fill)


def _draw_connector(
    draw: ImageDraw.ImageDraw,
    *,
    start: tuple[float, float],
    end: tuple[float, float],
    fill: tuple[int, int, int, int],
    width: int,
    offset: int,
) -> None:
    draw.line(
        (
            start[0] + offset,
            start[1] + offset,
            end[0] + offset,
            end[1] + offset,
        ),
        fill=fill,
        width=width,
    )


def _palette(
    level: TrayStatusLevel,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int], tuple[int, int, int, int]]:
    palettes = {
        TrayStatusLevel.ONLINE: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (58, 211, 122, 255)),
        TrayStatusLevel.BUSY: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (56, 163, 255, 255)),
        TrayStatusLevel.DEGRADED: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (255, 179, 71, 255)),
        TrayStatusLevel.OFFLINE: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (240, 87, 113, 255)),
        TrayStatusLevel.STARTING: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (108, 126, 255, 255)),
        TrayStatusLevel.STOPPED: ((246, 248, 250, 238), (104, 116, 128, 232), (0, 0, 0, 58), (163, 174, 184, 255)),
    }
    return palettes[level]
