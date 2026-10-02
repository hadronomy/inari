from odoo import api, models

from ..services import pos_binding_projection


class PosPrinter(models.Model):
    _inherit = "pos.printer"

    @api.model
    def _load_pos_data_read(self, records, config):
        values = super()._load_pos_data_read(records, config)
        if not values:
            return values
        bindings = (
            self.env["inari.device.binding"]
            .sudo()
            .search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("pos_config_id", "=", config.id),
                    ("pos_printer_id", "in", [value["id"] for value in values]),
                    ("purpose", "=", "pos_preparation"),
                    ("active", "=", True),
                    ("state", "=", "active"),
                ]
            )
        )
        bindings_by_printer = {
            binding.pos_printer_id.id: binding for binding in bindings
        }
        for value in values:
            binding = bindings_by_printer.get(value["id"])
            value["inari_preparation_binding"] = (
                pos_binding_projection(self.env, config, binding) if binding else False
            )
        return values
