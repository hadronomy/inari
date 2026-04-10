from odoo import api, fields, models


class PosConfig(models.Model):
    _inherit = "pos.config"

    iot_bridge_enabled = fields.Boolean(default=False)
    iot_bridge_agent_url = fields.Char(default="http://127.0.0.1:7310")
    iot_bridge_allowed_origin = fields.Char(
        default=lambda self: self._default_iot_bridge_allowed_origin()
    )
    iot_bridge_receipt_mode = fields.Selection(
        [("payload", "Payload (ESC/POS)"), ("html", "HTML passthrough")],
        default="payload",
        required=True,
    )
    iot_bridge_auto_print = fields.Boolean(default=True)
    iot_bridge_allow_manual_reprint = fields.Boolean(default=True)
    iot_bridge_open_cashdrawer = fields.Boolean(default=False)
    iot_bridge_timeout_ms = fields.Integer(default=4500)
    iot_bridge_debug = fields.Boolean(default=False)
    iot_bridge_default_printer_name = fields.Char()
    iot_bridge_kitchen_printer_name = fields.Char()
    iot_bridge_scale_enabled = fields.Boolean(default=False)
    iot_bridge_customer_display_enabled = fields.Boolean(default=False)
    iot_bridge_scanner_enabled = fields.Boolean(default=True)

    @api.model
    def _default_iot_bridge_allowed_origin(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return base_url

    def get_iot_bridge_public_config(self):
        self.ensure_one()
        return {
            "enabled": bool(self.iot_bridge_enabled),
            "agent_url": (self.iot_bridge_agent_url or "").rstrip("/"),
            "allowed_origin": self.iot_bridge_allowed_origin or "",
            "receipt_mode": self.iot_bridge_receipt_mode,
            "auto_print": bool(self.iot_bridge_auto_print),
            "allow_manual_reprint": bool(self.iot_bridge_allow_manual_reprint),
            "open_cashdrawer": bool(self.iot_bridge_open_cashdrawer),
            "timeout_ms": int(self.iot_bridge_timeout_ms or 4500),
            "debug": bool(self.iot_bridge_debug),
            "default_printer_name": self.iot_bridge_default_printer_name or "",
            "kitchen_printer_name": self.iot_bridge_kitchen_printer_name or "",
            "scale_enabled": bool(self.iot_bridge_scale_enabled),
            "customer_display_enabled": bool(self.iot_bridge_customer_display_enabled),
            "scanner_enabled": bool(self.iot_bridge_scanner_enabled),
        }
