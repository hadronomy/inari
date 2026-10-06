from __future__ import annotations

from io import BytesIO

from PIL import Image

from inari.printing.renderers import (
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


def test_render_centers_a_narrow_image_on_the_printable_width() -> None:
    buffer = BytesIO()
    Image.new("RGB", (8, 2), "black").save(buffer, format="PNG")
    renderer = EscPosImageReceiptRenderer(
        EscPosImageReceiptRendererConfig(
            max_width=24, trailing_feed_lines=0, cut_mode=None
        )
    )

    payload = renderer.render(buffer.getvalue(), mime_type="image/png")

    assert payload[6:10] == bytes((3, 0, 2, 0))
    assert payload[10:] == b"\x00\xff\x00" * 2


def test_render_feeds_the_complete_image_to_the_cutter() -> None:
    buffer = BytesIO()
    Image.new("RGB", (8, 2), "black").save(buffer, format="PNG")
    renderer = EscPosImageReceiptRenderer(
        EscPosImageReceiptRendererConfig(max_width=8)
    )

    payload = renderer.render(buffer.getvalue(), mime_type="image/png")

    assert payload[10:12] == b"\xff\xff"
    assert payload[12:] == b"\x1b\x64\x03\x1d\x56\x42\x00"
