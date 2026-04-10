from odoo import http
from odoo.http import request


class PosIotBridgeController(http.Controller):
    @http.route("/pos_iot_bridge/config", type="json", auth="user")
    def config(self, config_id=None):
        pos_config = request.env["pos.config"].browse(int(config_id)) if config_id else request.env.user.pos_config_id
        if not pos_config or not pos_config.exists():
            return {"ok": False, "code": "CONFIG_NOT_FOUND", "message": "POS config not found."}
        return {"ok": True, "config": pos_config.sudo().get_iot_bridge_public_config()}

    @http.route("/pos_iot_bridge/test_receipt", type="json", auth="user")
    def test_receipt(self, config_id=None):
        pos_config = request.env["pos.config"].browse(int(config_id)) if config_id else request.env.user.pos_config_id
        payload = {
            "name": "TEST/0001",
            "company": {"name": request.env.company.name},
            "headerData": {
                "company": request.env.company.name,
                "date_order": "1970-01-01 00:00:00",
                "cashier": request.env.user.name,
            },
            "orderlines": [
                {"product_name": "Connectivity check", "qty": 1, "price_display": "$1.00", "price_with_tax": 1.0},
                {"product_name": "Printer bridge", "qty": 1, "price_display": "$0.00", "price_with_tax": 0.0},
            ],
            "amount_total": 1.0,
            "amount_tax": 0.0,
            "amount_paid": 1.0,
            "amount_return": 0.0,
            "paymentlines": [{"name": "Cash", "amount": 1.0}],
            "footer": "POS IoT Bridge test receipt",
        }
        return {"ok": True, "receipt": payload, "config": pos_config.sudo().get_iot_bridge_public_config() if pos_config else {}}

    @http.route("/pos_iot_bridge/health", type="json", auth="user")
    def health(self, config_id=None):
        pos_config = request.env["pos.config"].browse(int(config_id)) if config_id else request.env.user.pos_config_id
        if not pos_config or not pos_config.exists():
            return {"ok": False, "code": "CONFIG_NOT_FOUND", "message": "POS config not found."}
        config = pos_config.sudo().get_iot_bridge_public_config()
        return {
            "ok": True,
            "config_valid": bool(config["enabled"] and config["agent_url"]),
            "config": config,
        }
