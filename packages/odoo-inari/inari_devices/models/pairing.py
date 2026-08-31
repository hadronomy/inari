from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class InariAgentEndpoint(models.Model):
    _name = "inari.agent.endpoint"
    _description = "Authenticated local Agent Endpoint"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    agent_id = fields.Many2one("inari.agent", required=True, ondelete="restrict", index=True)
    origin = fields.Char(required=True)
    endpoint_url = fields.Char(required=True)
    certificate_fingerprint = fields.Char(required=True)
    state = fields.Selection([("active", "Active"), ("revoked", "Revoked")], default="active", index=True)
    last_seen_at = fields.Datetime()

    _endpoint_origin_uniq = models.Constraint(
        "UNIQUE(company_id, agent_id, origin)",
        "An Agent origin can have one Endpoint.",
    )


class InariClientPairing(models.Model):
    _name = "inari.client.pairing"
    _description = "Inari Client Pairing"
    _order = "create_date desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict")
    site_id = fields.Many2one("inari.site", required=True, ondelete="restrict")
    agent_id = fields.Many2one("inari.agent", required=True, ondelete="restrict")
    pos_config_id = fields.Many2one("pos.config", required=True, ondelete="restrict")
    browser_key_thumbprint = fields.Char(required=True, index=True)
    origin = fields.Char(required=True)
    role = fields.Selection([("operator", "Device Operator"), ("manager", "Device Manager")], required=True)
    scope_json = fields.Json(required=True)
    state = fields.Selection([("pending", "Pending"), ("approved", "Approved"), ("denied", "Denied"), ("revoked", "Revoked"), ("expired", "Expired")], default="pending", index=True)
    approved_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    approved_at = fields.Datetime(readonly=True)
    expires_at = fields.Datetime(required=True)
    grant_ids = fields.One2many("inari.client.grant", "pairing_id")

    _pairing_key_uniq = models.Constraint(
        "UNIQUE(company_id, browser_key_thumbprint, origin, pos_config_id)",
        "A browser can have one pairing for a POS configuration.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("expires_at", fields.Datetime.now() + timedelta(minutes=10))
        records = super().create(vals_list)
        for record in records:
            if any(scope.company_id != record.company_id for scope in (record.organization_id, record.site_id, record.agent_id, record.pos_config_id)):
                raise ValidationError(_("A Client Pairing must use one company."))
        return records

    def action_approve(self):
        for record in self:
            if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_manager"):
                raise AccessError(_("A Device Manager must approve the Client Pairing."))
            if record.state != "pending":
                raise UserError(_("Only a pending Pairing Request can be approved."))
            if record.expires_at < fields.Datetime.now():
                record.state = "expired"
                raise UserError(_("The Pairing Request expired. Create a new request."))
            record.write({"state": "approved", "approved_by": self.env.uid, "approved_at": fields.Datetime.now()})
        return True

    def action_deny(self):
        self.write({"state": "denied"})
        return True

    def action_revoke(self):
        self.write({"state": "revoked"})
        self.mapped("grant_ids").action_revoke()
        return True


class InariPairingRequest(models.Model):
    _name = "inari.pairing.request"
    _description = "Inari Pairing Request"
    _order = "create_date desc"

    pairing_id = fields.Many2one("inari.client.pairing", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="pairing_id.company_id", store=True, required=True, index=True, readonly=True)
    assertion_jti = fields.Char(required=True, index=True)
    assertion = fields.Json(required=True)
    state = fields.Selection([("pending", "Pending"), ("approved", "Approved"), ("denied", "Denied"), ("expired", "Expired")], default="pending")
    expires_at = fields.Datetime(required=True)

    _assertion_jti_uniq = models.Constraint(
        "UNIQUE(assertion_jti)",
        "A Pairing Assertion can be used once.",
    )

    def action_approve(self):
        for record in self:
            record.pairing_id.action_approve()
            record.state = "approved"
        return True


class InariClientGrant(models.Model):
    _name = "inari.client.grant"
    _description = "Inari Client Grant"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    pairing_id = fields.Many2one("inari.client.pairing", required=True, ondelete="cascade", index=True)
    grant_id = fields.Char(required=True, copy=False, index=True)
    authorization_digest = fields.Char(required=True, copy=False, index=True)
    scopes = fields.Json(required=True)
    issued_at = fields.Datetime(required=True, default=fields.Datetime.now)
    expires_at = fields.Datetime(required=True)
    state = fields.Selection([("active", "Active"), ("revoked", "Revoked"), ("expired", "Expired")], default="active", index=True)
    revoked_at = fields.Datetime(readonly=True)

    _grant_id_uniq = models.Constraint(
        "UNIQUE(company_id, grant_id)",
        "The Client Grant identity must be unique.",
    )

    def action_revoke(self):
        self.write({"state": "revoked", "revoked_at": fields.Datetime.now()})
        return True
