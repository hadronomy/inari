from __future__ import annotations

from io import BytesIO

from PIL import Image

from inari.receipt_renderers import (
    EscPosImageReceiptRenderer,
    EscPosImageReceiptRendererConfig,
)


def test_render_converts_image_to_raster_command() -> None:
    image = Image.new("RGB", (8, 8), "white")
    for x in range(4):
        for y in range(8):
            image.putpixel((x, y), (0, 0, 0))

    buffer = BytesIO()
    image.save(buffer, format="PNG")

    renderer = EscPosImageReceiptRenderer(
        EscPosImageReceiptRendererConfig(
            max_width=8,
            trailing_feed_lines=0,
            cut_mode=None,
        )
    )
    payload = renderer.render(buffer.getvalue(), mime_type="image/png")

    assert payload.startswith(b"\x1b@\x1d\x76\x30\x00")
    assert b"\xf0" in payload
