from odoo import fields, models

from ..services import pos_binding_projection


class PosConfig(models.Model):
    _inherit = "pos.config"

    inari_receipt_binding = fields.Json(
        compute="_compute_inari_receipt_binding",
        compute_sudo=True,
    )

    def _compute_inari_receipt_binding(self):
        Binding = self.env["inari.device.binding"].sudo()
        for config in self:
            binding = Binding.search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("pos_config_id", "=", config.id),
                    ("purpose", "=", "pos_receipt"),
                    ("active", "=", True),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
            config.inari_receipt_binding = pos_binding_projection(
                self.env, config, binding
            )
