from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class InariProjection(models.AbstractModel):
    _name = "inari.projection"
    _description = "Inari projection helpers"
    _abstract = True

    company_id = fields.Many2one("res.company", required=True, index=True, ondelete="restrict")
    controller_uuid = fields.Char(required=True, copy=False, index=True)
    active = fields.Boolean(default=True, index=True)
    controller_version = fields.Integer(default=0, readonly=True)
    last_synced_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_sync"):
            raise UserError(_("Only the Inari sync identity can create projections."))
        return super().create(vals_list)

    def write(self, vals):
        if "company_id" in vals and any(record.company_id.id != vals["company_id"] for record in self):
            raise ValidationError(_("A projection cannot move between companies."))
        if set(vals) - {"active", "controller_version", "last_synced_at"}:
            if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_sync"):
                raise UserError(_("Projection fields are managed by the Controller."))
        return super().write(vals)

    def unlink(self):
        raise UserError(_("Projections are archived by synchronization and cannot be deleted."))

    def archive_absent(self):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_sync"):
            raise UserError(_("The Inari sync identity is required."))
        self.write({"active": False, "last_synced_at": fields.Datetime.now()})
        return True

    @api.model
    def sync_values(self, values, version=0):
        """Create or update one projection without moving its company scope."""
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_sync"):
            raise UserError(_("The Inari sync identity is required."))
        values = dict(values)
        record = self.search(
            [("company_id", "=", values["company_id"]), ("controller_uuid", "=", values["controller_uuid"])], limit=1
        )
        values.update(controller_version=version, last_synced_at=fields.Datetime.now())
        if record:
            if record.company_id.id != values.get("company_id", record.company_id.id):
                raise ValidationError(_("A projection cannot move between companies."))
            record.write(values)
            return record
        return self.create(values)


class InariOrganization(models.Model):
    _name = "inari.organization"
    _inherit = "inari.projection"
    _description = "Inari Organization projection"
    _controller_uuid_company_uniq = models.Constraint(
        "UNIQUE(company_id, controller_uuid)",
        "The Organization key must be unique per company.",
    )
    _one_organization_per_company = models.Constraint(
        "UNIQUE(company_id)",
        "A company can map to one Organization.",
    )

    name = fields.Char(required=True, readonly=True)
    site_ids = fields.One2many("inari.site", "organization_id")
    agent_ids = fields.One2many("inari.agent", "organization_id")


class InariSite(models.Model):
    _name = "inari.site"
    _inherit = "inari.projection"
    _description = "Inari Site projection"
    _controller_uuid_company_uniq = models.Constraint(
        "UNIQUE(company_id, controller_uuid)",
        "The Site key must be unique per company.",
    )

    name = fields.Char(required=True, readonly=True)
    code = fields.Char(readonly=True)
    organization_id = fields.Many2one("inari.organization", required=True, readonly=True, ondelete="restrict")
    agent_ids = fields.One2many("inari.agent", "site_id")

    @api.constrains("organization_id", "company_id")
    def _check_organization_company(self):
        for record in self:
            if record.organization_id.company_id != record.company_id:
                raise ValidationError(_("The Site and Organization must use the same company."))


class InariAgent(models.Model):
    _name = "inari.agent"
    _inherit = "inari.projection"
    _description = "Inari Agent projection"
    _controller_uuid_company_uniq = models.Constraint(
        "UNIQUE(company_id, controller_uuid)",
        "The Agent key must be unique per company.",
    )
    _agent_id_company_uniq = models.Constraint(
        "UNIQUE(company_id, agent_id)",
        "The Agent identity must be unique per company.",
    )

    name = fields.Char(required=True, readonly=True)
    agent_id = fields.Char(required=True, readonly=True, index=True)
    organization_id = fields.Many2one("inari.organization", required=True, readonly=True, ondelete="restrict")
    site_id = fields.Many2one("inari.site", required=True, readonly=True, ondelete="restrict")
    endpoint_url = fields.Char(readonly=True)
    certificate_fingerprint = fields.Char(readonly=True)
    boot_identity = fields.Char(readonly=True)
    connection_state = fields.Selection(
        [("online", "Online"), ("offline", "Offline"), ("awaiting_first_contact", "Awaiting first contact")],
        readonly=True,
    )
    last_seen_at = fields.Datetime(readonly=True)
    capabilities_count = fields.Integer(compute="_compute_capabilities_count")
    device_ids = fields.One2many("inari.device", "agent_id")

    def _compute_capabilities_count(self):
        for record in self:
            record.capabilities_count = self.env["inari.device.capability"].search_count(
                [("device_id.agent_id", "=", record.id)]
            )

    @api.constrains("organization_id", "site_id", "company_id")
    def _check_scope(self):
        for record in self:
            if record.organization_id.company_id != record.company_id or record.site_id.company_id != record.company_id:
                raise ValidationError(_("The Agent scope must use one company and Organization."))


class InariDevice(models.Model):
    _name = "inari.device"
    _inherit = "inari.projection"
    _description = "Inari Device projection"
    _controller_uuid_company_uniq = models.Constraint(
        "UNIQUE(company_id, controller_uuid)",
        "The Device key must be unique per company.",
    )
    _device_id_company_uniq = models.Constraint(
        "UNIQUE(company_id, agent_id, device_id)",
        "The Device identity must be unique per Agent and company.",
    )

    name = fields.Char(required=True, readonly=True)
    device_id = fields.Char(required=True, readonly=True, index=True)
    organization_id = fields.Many2one("inari.organization", required=True, readonly=True, ondelete="restrict")
    site_id = fields.Many2one("inari.site", required=True, readonly=True, ondelete="restrict")
    agent_id = fields.Many2one("inari.agent", required=True, readonly=True, ondelete="restrict")
    kind = fields.Selection([(value, value.capitalize()) for value in ("printer", "scale", "scanner", "display")], readonly=True)
    device_class = fields.Selection([("physical", "Physical"), ("virtual", "Virtual")], readonly=True)
    connection_state = fields.Selection(
        [(value, value.replace("_", " ").capitalize()) for value in ("discovered", "pending_approval", "online", "offline", "degraded", "blocked")],
        readonly=True,
    )
    transport = fields.Selection([(value, value.upper() if value in ("usb", "hid") else value.capitalize()) for value in ("spooler", "network", "usb", "hid", "serial")], readonly=True)
    health_state = fields.Selection(
        [("ready", "Ready"), ("degraded", "Degraded"), ("offline", "Offline"), ("unknown", "Unknown")],
        default="unknown", readonly=True, index=True,
    )
    health_reason = fields.Char(readonly=True)
    driver_profile_digest = fields.Char(readonly=True)
    last_observed_at = fields.Datetime(readonly=True)
    capability_ids = fields.One2many("inari.device.capability", "device_id")

    @api.constrains("organization_id", "site_id", "agent_id", "company_id")
    def _check_scope(self):
        for record in self:
            if any(scope.company_id != record.company_id for scope in (record.organization_id, record.site_id, record.agent_id)):
                raise ValidationError(_("The Device scope must use one company."))
            if record.agent_id.site_id != record.site_id or record.agent_id.organization_id != record.organization_id:
                raise ValidationError(_("The Device Agent, Site, and Organization must match."))


class InariDeviceCapability(models.Model):
    _name = "inari.device.capability"
    _inherit = "inari.projection"
    _description = "Inari Device Capability projection"
    _capability_uniq = models.Constraint(
        "UNIQUE(company_id, device_id, operation, contract_major)",
        "The Device Capability must be unique.",
    )

    device_id = fields.Many2one("inari.device", required=True, readonly=True, ondelete="restrict")
    operation = fields.Selection(
        [("receipt_image", "Receipt image"), ("report_pdf", "Report PDF"), ("label_document", "Label document"),
         ("open_cash_drawer", "Open cash drawer"), ("scale_reading", "Scale reading"), ("barcode_event", "Barcode event")],
        required=True, readonly=True,
    )
    contract_major = fields.Integer(required=True, readonly=True)
    media_types = fields.Json(readonly=True)
    output_evidence = fields.Selection(
        [("transport", "Transport"), ("spooler", "Spooler"), ("device", "Device")], readonly=True
    )
    sharing_mode = fields.Selection([("shared", "Shared"), ("exclusive", "Exclusive")], readonly=True)
    limits = fields.Json(readonly=True)
    driver_version = fields.Char(readonly=True)
    certification_ref = fields.Char(readonly=True)


class InariManagedWork(models.Model):
    _name = "inari.managed.work"
    _inherit = "inari.projection"
    _description = "Managed Work projection"
    _managed_work_key_uniq = models.Constraint(
        "UNIQUE(company_id, organization_id, idempotency_key)",
        "The Idempotency Key must be unique.",
    )

    organization_id = fields.Many2one("inari.organization", required=True, readonly=True, ondelete="restrict")
    site_id = fields.Many2one("inari.site", required=True, readonly=True, ondelete="restrict")
    agent_id = fields.Many2one("inari.agent", required=True, readonly=True, ondelete="restrict")
    device_id = fields.Many2one("inari.device", readonly=True, ondelete="restrict")
    managed_work_id = fields.Char(required=True, readonly=True, index=True)
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    payload_fingerprint = fields.Char(required=True, readonly=True)
    operation = fields.Selection([("report_pdf", "Report PDF"), ("label_document", "Label document"), ("receipt_image", "Receipt image")], readonly=True)
    state = fields.Selection(
        [("pending_agent", "Pending Agent"), ("dispatching", "Dispatching"), ("accepted", "Accepted"),
         ("rejected", "Rejected"), ("canceled", "Canceled"), ("expired", "Expired"), ("recovery_uncertain", "Recovery uncertain")],
        default="pending_agent", readonly=True, index=True,
    )
    expires_at = fields.Datetime(readonly=True)
    accepted_at = fields.Datetime(readonly=True)
    print_intent_id = fields.Char(readonly=True)
    print_job_id = fields.Char(readonly=True)
    error_code = fields.Char(readonly=True)

    def transition(self, state, **values):
        for record in self:
            allowed = {
                "pending_agent": {"dispatching", "rejected", "canceled", "expired"},
                "dispatching": {"accepted", "rejected", "recovery_uncertain"},
                "recovery_uncertain": {"accepted", "canceled", "expired"},
            }
            if state not in allowed.get(record.state, set()):
                raise ValidationError(_("The Managed Work transition is not allowed."))
            record.write(dict(values, state=state))
        return True

    def action_cancel(self):
        for record in self:
            if record.state != "pending_agent":
                raise ValidationError(_("Managed Work can be canceled only before dispatch."))
            record.transition("canceled")
        return True

    def action_expire(self):
        for record in self:
            if record.state not in {"pending_agent", "dispatching"}:
                raise ValidationError(_("Only non-terminal Managed Work can expire."))
            record.transition("expired")
        return True
