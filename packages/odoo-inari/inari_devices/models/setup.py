from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class InariWorkloadIdentity(models.Model):
    _name = "inari.workload.identity"
    _description = "Organization Workload Identity"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict")
    client_id = fields.Char(required=True)
    issuer_url = fields.Char(required=True)
    state = fields.Selection([("draft", "Draft"), ("active", "Active"), ("revoked", "Revoked")], default="draft", index=True)
    last_token_at = fields.Datetime(readonly=True)

    _one_identity_per_company = models.Constraint(
        "UNIQUE(company_id)",
        "A company has one Organization Workload Identity.",
    )
    _client_id_uniq = models.Constraint(
        "UNIQUE(client_id)",
        "The workload client identity must be unique.",
    )

    @api.constrains("company_id", "organization_id")
    def _check_scope(self):
        for record in self:
            if record.organization_id.company_id != record.company_id:
                raise ValidationError(_("The workload identity must match its Organization company."))

    def action_activate(self):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_admin"):
            raise AccessError(_("A System Administrator is required to activate workload identity."))
        self.write({"state": "active"})
        return True

    def action_revoke(self):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_admin"):
            raise AccessError(_("A System Administrator is required to revoke workload identity."))
        self.write({"state": "revoked"})
        return True


class InariSetupState(models.Model):
    _name = "inari.setup.state"
    _description = "Inari setup and decommission state"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    state = fields.Selection([("not_ready", "Not ready"), ("ready", "Ready"), ("blocked", "Blocked"), ("decommissioned", "Decommissioned")], default="not_ready", index=True)
    contract_major = fields.Integer(default=1)
    addon_version = fields.Char(default="19.0.1.0.0")
    controller_url = fields.Char()
    last_check_at = fields.Datetime(readonly=True)
    last_error = fields.Char()
    decommission_run_id = fields.Many2one("inari.decommission.run", readonly=True, ondelete="restrict")

    _one_setup_per_company = models.Constraint(
        "UNIQUE(company_id)",
        "A company has one Inari setup state.",
    )

    def action_check_readiness(self):
        for record in self:
            identity = self.env["inari.workload.identity"].search([("company_id", "=", record.company_id.id), ("state", "=", "active")], limit=1)
            organization = self.env["inari.organization"].search([("company_id", "=", record.company_id.id), ("active", "=", True)], limit=1)
            if not identity or not organization or not record.controller_url:
                record.write({"state": "blocked", "last_error": "workload_identity, Organization, or Controller URL is missing", "last_check_at": fields.Datetime.now()})
                continue
            record.write({"state": "ready", "last_error": False, "last_check_at": fields.Datetime.now()})
        return True

    @api.model
    def removal_guard(self):
        """Return false unless every company has a completed database run."""
        companies = self.env["res.company"].search([])
        runs = self.env["inari.decommission.run"].search([("scope", "=", "database"), ("phase", "=", "complete")])
        covered = runs.mapped("company_id")
        if any(company not in covered for company in companies):
            raise UserError(_("A completed database Decommission Run is required before removal."))
        return True


class InariReleaseSet(models.Model):
    _name = "inari.release.set"
    _description = "Signed Inari Release Set"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict")
    release_id = fields.Char(required=True, index=True)
    manifest_digest = fields.Char(required=True)
    contract_major = fields.Integer(required=True)
    schema_revision = fields.Char(required=True)
    signature_identity = fields.Char(required=True)
    state = fields.Selection([("draft", "Draft"), ("active", "Active"), ("revoked", "Revoked")], default="draft", index=True)
    activated_at = fields.Datetime(readonly=True)

    _release_id_company_uniq = models.Constraint(
        "UNIQUE(company_id, release_id)",
        "The Release Set identity must be unique per company.",
    )

    def action_activate(self):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_admin"):
            raise AccessError(_("A System Administrator is required to activate a Release Set."))
        self.write({"state": "active", "activated_at": fields.Datetime.now()})
        return True


class InariAlert(models.Model):
    _name = "inari.alert"
    _description = "Inari operational alert"
    _order = "create_date desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    severity = fields.Selection([("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], required=True)
    code = fields.Char(required=True, index=True)
    message = fields.Char(required=True)
    state = fields.Selection([("open", "Open"), ("acknowledged", "Acknowledged"), ("closed", "Closed")], default="open", index=True)
    acknowledged_by = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    acknowledged_at = fields.Datetime(readonly=True)

    def action_acknowledge(self):
        self.write({"state": "acknowledged", "acknowledged_by": self.env.uid, "acknowledged_at": fields.Datetime.now()})
        return True

    def action_close(self):
        self.write({"state": "closed"})
        return True
