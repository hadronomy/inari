import unittest

from iot_agent.receipt_renderers import EscPosRenderer, EscPosRendererConfig


class EscPosRendererTests(unittest.TestCase):
    def test_render_basic_receipt(self) -> None:
        renderer = EscPosRenderer()

        payload = renderer.render(
            {
                "name": "TEST/1",
                "headerData": {"company": "Demo Shop", "date_order": "2026-04-09 12:00:00"},
                "orderlines": [{"product_name": "Coffee", "qty": 2, "price_display": "3.00"}],
                "amount_tax": 0.30,
                "amount_total": 3.00,
                "amount_paid": 5.00,
                "amount_return": 2.00,
            }
        )

        self.assertTrue(payload.startswith(b"\x1b@"))
        self.assertIn(b"Demo Shop", payload)
        self.assertIn(b"Coffee", payload)
        self.assertIn(b"Total", payload)
        self.assertTrue(payload.endswith(b"\x1d\x56\x01"))

    def test_render_wraps_long_product_names(self) -> None:
        renderer = EscPosRenderer(EscPosRendererConfig(line_width=16, trailing_feed_lines=0, cut_mode=None))

        payload = renderer.render(
            {
                "orderlines": [
                    {
                        "product_name": "House Blend Coffee Beans Extra Dark Roast",
                        "qty": 1,
                        "price_display": "12.50",
                    }
                ],
                "amount_tax": 0,
                "amount_total": 12.50,
            }
        )

        self.assertIn(b"House Blend", payload)
        self.assertIn(b"Coffee Beans", payload)
        self.assertIn(b"12.50", payload)


if __name__ == "__main__":
    unittest.main()
