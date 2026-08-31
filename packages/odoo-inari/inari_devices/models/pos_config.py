from urllib.parse import urlsplit

from odoo import fields, models


def _is_https_origin(value):
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


class PosConfig(models.Model):
    _inherit = "pos.config"

    inari_receipt_binding = fields.Json(
        compute="_compute_inari_receipt_binding",
        compute_sudo=True,
    )

    def _compute_inari_receipt_binding(self):
        Binding = self.env["inari.device.binding"].sudo()
        Endpoint = self.env["inari.agent.endpoint"].sudo()
        browser_origin = self.get_base_url().rstrip("/")
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
            revision = binding.active_revision_id
            if not revision or revision.state != "active":
                config.inari_receipt_binding = False
                continue
            agent = revision.device_id.agent_id
            endpoint = Endpoint.search(
                [
                    ("company_id", "=", config.company_id.id),
                    ("agent_id", "=", agent.id),
                    ("origin", "=", browser_origin),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
            endpoint_url = (
                endpoint.endpoint_url
                if _is_https_origin(endpoint.endpoint_url)
                else False
            )
            config.inari_receipt_binding = {
                "authoritative": True,
                "state": "ready" if endpoint_url else "agent_endpoint_required",
                "binding_revision_id": revision.revision_id,
                "device_id": revision.device_id.device_id,
                "agent_id": agent.agent_id,
                "agent_endpoint": endpoint_url,
                "authorization_digest": revision.authorization_digest,
                "capability_id": revision.capability_id.controller_uuid,
                "contract_major": revision.capability_id.contract_major,
                "driver_profile_digest": revision.driver_profile_digest,
            }
