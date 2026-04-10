from odoo_iot_agent.app.receipt_renderers.escpos_renderer import EscPosRenderer


def test_render_basic_receipt():
    renderer = EscPosRenderer()
    payload = renderer.render({
        "name": "TEST/1",
        "headerData": {"company": "Demo Shop", "date_order": "2026-04-09 12:00:00"},
        "orderlines": [{"product_name": "Coffee", "qty": 2, "price_display": "3.00"}],
        "amount_tax": 0.3,
        "amount_total": 3.0,
        "amount_paid": 5.0,
        "amount_return": 2.0,
    })
    assert payload.startswith(b"\x1b@")
    assert b"Demo Shop" in payload
    assert b"Coffee" in payload
