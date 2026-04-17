from __future__ import annotations

from iot_agent.receipt_renderers import EscPosRenderer, EscPosRendererConfig


def test_render_basic_receipt() -> None:
    renderer = EscPosRenderer()

    payload = renderer.render(
        {
            "name": "TEST/1",
            "headerData": {"company": "Demo Shop", "date_order": "2026-04-09 12:00:00"},
            "orderlines": [
                {"product_name": "Coffee", "qty": 2, "price_display": "3.00"}
            ],
            "amount_tax": 0.30,
            "amount_total": 3.00,
            "amount_paid": 5.00,
            "amount_return": 2.00,
        }
    )

    assert payload.startswith(b"\x1b@")
    assert b"Demo Shop" in payload
    assert b"Coffee" in payload
    assert b"Total" in payload
    assert payload.endswith(b"\x1d\x56\x01")


def test_render_wraps_long_product_names() -> None:
    renderer = EscPosRenderer(
        EscPosRendererConfig(line_width=16, trailing_feed_lines=0, cut_mode=None)
    )

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

    assert b"House Blend" in payload
    assert b"Coffee Beans" in payload
    assert b"12.50" in payload
