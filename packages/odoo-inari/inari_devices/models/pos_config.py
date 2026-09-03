from odoo import fields, models

from ..services import pos_binding_projection


class PosConfig(models.Model):
    _inherit = "pos.config"

    inari_receipt_binding = fields.Json(
        compute="_compute_inari_receipt_binding",
        compute_sudo=True,
    )
    inari_cash_drawer_binding = fields.Json(
        compute="_compute_inari_cash_drawer_binding",
        compute_sudo=True,
    )
    inari_scale_binding = fields.Json(
        compute="_compute_inari_scale_binding",
        compute_sudo=True,
    )
    inari_scanner_binding = fields.Json(
        compute="_compute_inari_scanner_binding",
        compute_sudo=True,
    )

    def _compute_inari_binding(self, purpose, field_name):
        Binding = self.env["inari.device.binding"].sudo()
        for config in self:
            binding = Binding.search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("pos_config_id", "=", config.id),
                    ("purpose", "=", purpose),
                    ("active", "=", True),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
            setattr(
                config,
                field_name,
                pos_binding_projection(self.env, config, binding),
            )

    def _compute_inari_receipt_binding(self):
        self._compute_inari_binding("pos_receipt", "inari_receipt_binding")

    def _compute_inari_cash_drawer_binding(self):
        self._compute_inari_binding("pos_cash_drawer", "inari_cash_drawer_binding")

    def _compute_inari_scale_binding(self):
        self._compute_inari_binding("pos_scale", "inari_scale_binding")

    def _compute_inari_scanner_binding(self):
        self._compute_inari_binding("pos_scanner", "inari_scanner_binding")
