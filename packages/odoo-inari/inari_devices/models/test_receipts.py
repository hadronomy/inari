from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from ..services.pos_binding_projections import pos_binding_projection


class InariPrinterTestReceipts(models.Model):
    _inherit = "inari.device"

    def _check_test_receipt_access(self):
        if not self.env.is_superuser() and not self.env.user.has_group(
            "inari_devices.group_inari_manager"
        ):
            raise AccessError(_("A Device Manager is required to print test receipts."))
        self.check_access("read")

    def action_test_receipts(self):
        self._check_test_receipt_access()
        if len(self) == 1 and self.kind != "printer":
            raise UserError(_("Select a printer to print test receipts."))
        return {
            "type": "ir.actions.client",
            "tag": "inari_devices.test_receipts",
            "params": {"device_id": self.id if len(self) == 1 else False},
        }

    @api.model
    def get_test_receipt_options(self, device_id=False):
        """Read permitted printers and their existing receipt authorization."""
        self._check_test_receipt_access()
        domain = [
            ("kind", "=", "printer"),
            ("active", "=", True),
            ("company_id", "in", self.env.companies.ids),
        ]
        if device_id:
            device = self.browse(device_id).exists()
            device._check_test_receipt_access()
            domain.append(("id", "=", device.id))
        printers = []
        for device in self.search(domain, order="name, id"):
            bindings = self.env["inari.device.binding"].search(
                [
                    ("company_id", "=", device.company_id.id),
                    ("active_revision_id.device_id", "=", device.id),
                    ("active_revision_id.state", "=", "active"),
                    ("purpose", "in", ["pos_receipt", "pos_preparation"]),
                    ("state", "=", "active"),
                    ("active", "=", True),
                ],
                order="pos_config_id, id",
            )
            channels = []
            for binding in bindings:
                session = self.env["pos.session"].sudo().search(
                    [
                        ("config_id", "=", binding.pos_config_id.id),
                        ("company_id", "=", device.company_id.id),
                        ("state", "=", "opened"),
                    ],
                    limit=1,
                )
                projection = pos_binding_projection(
                    self.env, binding.pos_config_id, binding
                )
                if session and projection and projection["state"] == "ready":
                    channels.append(
                        {
                            "binding": projection,
                            "pos_session_id": str(session.id),
                            "label": binding.pos_config_id.display_name,
                        }
                    )
            printers.append(
                {
                    "id": device.id,
                    "device_id": device.device_id,
                    "name": device.name,
                    "company": device.company_id.name,
                    "channels": channels,
                    "unavailable_reason": (
                        False
                        if channels
                        else _(
                            "Open a POS session with an active receipt Binding "
                            "and a trusted Agent endpoint for this printer."
                        )
                    ),
                }
            )
        return printers
