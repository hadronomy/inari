from __future__ import annotations

import unittest
from io import BytesIO

from PIL import Image

from iot_agent.receipt_renderers import EscPosImageReceiptRenderer, EscPosImageReceiptRendererConfig


class EscPosImageReceiptRendererTests(unittest.TestCase):
    def test_render_converts_image_to_raster_command(self) -> None:
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

        self.assertTrue(payload.startswith(b"\x1b@\x1d\x76\x30\x00"))
        self.assertIn(b"\xf0", payload)


if __name__ == "__main__":
    unittest.main()
