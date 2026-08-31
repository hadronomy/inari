import hashlib
import json
from uuid import uuid4

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


PURPOSES = {
    "pos_receipt": "pos_config",
    "pos_preparation": "pos_config",
    "pos_cash_drawer": "pos_config",
    "pos_scale": "pos_config",
    "pos_scanner": "pos_config",
    "report_pdf": "site",
    "stock_label": "site",
}
PURPOSE_OPERATIONS = {
    "pos_receipt": "receipt_image",
    "pos_cash_drawer": "open_cashbox",
    "pos_scale": "scale_reading",
    "pos_scanner": "barcode_event",
    "report_pdf": "report_pdf",
    "stock_label": "label_document",
}


class InariDeviceBinding(models.Model):
    _name = "inari.device.binding"
    _description = "Inari Device Binding"
    _order = "company_id, site_id, purpose, id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="restrict",
    )
    site_id = fields.Many2one(
        "inari.site", required=True, ondelete="restrict", index=True
    )
    scope_type = fields.Selection(
        [("pos_config", "POS configuration"), ("site", "Site")],
        required=True,
        index=True,
    )
    purpose = fields.Selection(sorted(PURPOSES.items()), required=True, index=True)
    pos_config_id = fields.Many2one("pos.config", ondelete="restrict")
    pos_printer_id = fields.Many2one("pos.printer", ondelete="restrict")
    active = fields.Boolean(default=True, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("attention", "Needs attention"),
            ("disabled", "Disabled"),
        ],
        default="draft",
        index=True,
    )
    revision_ids = fields.One2many(
        "inari.device.binding.revision", "binding_id", copy=False
    )
    active_revision_id = fields.Many2one(
        "inari.device.binding.revision", ondelete="restrict", copy=False
    )
    latest_observed_test_id = fields.Many2one(
        "inari.device.test.result", ondelete="restrict", copy=False
    )
    latest_passed_test_id = fields.Many2one(
        "inari.device.test.result", ondelete="restrict", copy=False
    )
    revision_count = fields.Integer(compute="_compute_revision_count")

    _active_revision_binding_uniq = models.Constraint(
        "UNIQUE(active_revision_id)",
        "A Binding Revision belongs to one active Binding.",
    )

    def init(self):
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS inari_binding_pos_purpose_active_idx "
            "ON inari_device_binding (company_id, pos_config_id, purpose) "
            "WHERE active AND scope_type = 'pos_config' AND purpose <> 'pos_preparation'"
        )
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS inari_binding_preparation_printer_active_idx "
            "ON inari_device_binding (company_id, pos_printer_id) "
            "WHERE active AND purpose = 'pos_preparation'"
        )
    def _compute_revision_count(self):
        for record in self:
            record.revision_count = len(record.revision_ids)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._check_shape()
        return records

    def write(self, vals):
        if (
            not self.env.is_superuser()
            and self.env.user.has_group("inari_devices.group_inari_operator")
            and not self.env.user.has_group("inari_devices.group_inari_manager")
        ):
            raise AccessError(_("Device Operators cannot change Device Bindings."))
        result = super().write(vals)
        self._check_shape()
        return result

    @api.constrains(
        "scope_type",
        "purpose",
        "pos_config_id",
        "pos_printer_id",
        "company_id",
        "site_id",
    )
    def _check_shape(self):
        for record in self:
            expected = PURPOSES.get(record.purpose)
            if expected and expected != record.scope_type:
                raise ValidationError(
                    _("The Device Purpose requires a %s scope.", expected)
                )
            if record.scope_type == "pos_config" and not record.pos_config_id:
                raise ValidationError(
                    _("A POS configuration is required for this Binding.")
                )
            if record.scope_type == "site" and (
                record.pos_config_id or record.pos_printer_id
            ):
                raise ValidationError(
                    _("A Site Binding cannot reference a POS record.")
                )
            if record.purpose != "pos_preparation" and record.pos_printer_id:
                raise ValidationError(
                    _("Only preparation Bindings can reference a POS printer.")
                )
            if record.purpose == "pos_preparation" and not record.pos_printer_id:
                raise ValidationError(
                    _("A preparation Binding requires a POS printer.")
                )
            if (
                record.pos_printer_id
                and record.pos_config_id
                and record.pos_printer_id.config_id != record.pos_config_id
            ):
                raise ValidationError(
                    _("The preparation printer must belong to the POS configuration.")
                )
            if record.site_id.company_id != record.company_id:
                raise ValidationError(
                    _("The Site and Binding must use the same company.")
                )

    def action_create_revision(
        self, device, capability, normalized_options=None, driver_profile_digest=None
    ):
        self.ensure_one()
        if not self.env.is_superuser() and not self.env.user.has_group(
            "inari_devices.group_inari_manager"
        ):
            raise AccessError(
                _("A Device Manager is required to create a Binding Revision.")
            )
        if device.company_id != self.company_id or capability.device_id != device:
            raise ValidationError(
                _(
                    "The Device and Capability must match the Binding company and Device."
                )
            )
        expected_operation = PURPOSE_OPERATIONS.get(self.purpose)
        if expected_operation and capability.operation != expected_operation:
            raise ValidationError(
                _("The Device Capability does not support this Binding Purpose.")
            )
        normalized_options = normalized_options or {}
        digest_input = {
            "device": device.device_id,
            "capability": capability.id,
            "profile": driver_profile_digest or device.driver_profile_digest or "",
            "options": normalized_options,
            "purpose": self.purpose,
            "scope": self.scope_type,
            "site": self.site_id.controller_uuid,
        }
        digest = hashlib.sha256(
            json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        existing = self.revision_ids.filtered(
            lambda revision: revision.authorization_digest == digest
        )
        if existing:
            return existing[-1]
        revision = self.env["inari.device.binding.revision"].create(
            {
                "binding_id": self.id,
                "company_id": self.company_id.id,
                "site_id": self.site_id.id,
                "device_id": device.id,
                "capability_id": capability.id,
                "driver_profile_digest": driver_profile_digest
                or device.driver_profile_digest,
                "normalized_options": normalized_options,
                "authorization_digest": digest,
                "revision_number": max(
                    self.revision_ids.mapped("revision_number") or [0]
                )
                + 1,
                "state": "draft",
            }
        )
        return revision

    def action_activate_revision(self, revision):
        self.ensure_one()
        if revision.binding_id != self or revision.company_id != self.company_id:
            raise ValidationError(
                _("The Binding Revision does not belong to this Binding.")
            )
        if (
            revision.state != "tested"
            or revision.latest_passed_test_id.result != "passed"
        ):
            raise UserError(_("A passed Device Test is required before activation."))
        if self.active_revision_id and self.active_revision_id != revision:
            self.active_revision_id.write({"state": "inactive"})
        revision.write({"state": "active"})
        self.write(
            {"active_revision_id": revision.id, "active": True, "state": "active"}
        )
        return revision

    def action_deactivate(self):
        for record in self:
            if record.active_revision_id:
                record.active_revision_id.write({"state": "inactive"})
            record.write(
                {"active_revision_id": False, "active": False, "state": "disabled"}
            )
        return True


class InariDeviceBindingRevision(models.Model):
    _name = "inari.device.binding.revision"
    _description = "Immutable Inari Device Binding Revision"
    _order = "binding_id, revision_number desc"

    binding_id = fields.Many2one(
        "inari.device.binding", required=True, readonly=True, ondelete="restrict"
    )
    revision_id = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        index=True,
        default=lambda self: str(uuid4()),
    )
    purpose = fields.Selection(related="binding_id.purpose", store=True, readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict"
    )
    site_id = fields.Many2one(
        "inari.site", required=True, readonly=True, ondelete="restrict"
    )
    device_id = fields.Many2one(
        "inari.device", required=True, readonly=True, ondelete="restrict"
    )
    capability_id = fields.Many2one(
        "inari.device.capability", required=True, readonly=True, ondelete="restrict"
    )
    driver_profile_digest = fields.Char(readonly=True)
    normalized_options = fields.Json(readonly=True)
    authorization_digest = fields.Char(required=True, readonly=True, index=True)
    revision_number = fields.Integer(required=True, readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("tested", "Tested"),
            ("active", "Active"),
            ("inactive", "Inactive"),
            ("attention", "Needs attention"),
        ],
        default="draft",
        readonly=True,
        index=True,
    )
    test_result_ids = fields.One2many(
        "inari.device.test.result", "binding_revision_id", readonly=True
    )
    latest_observed_test_id = fields.Many2one(
        "inari.device.test.result", readonly=True, ondelete="restrict"
    )
    latest_passed_test_id = fields.Many2one(
        "inari.device.test.result", readonly=True, ondelete="restrict"
    )

    _revision_id_uniq = models.Constraint(
        "UNIQUE(revision_id)",
        "The Binding Revision identity must be unique.",
    )
    _revision_number_uniq = models.Constraint(
        "UNIQUE(binding_id, revision_number)",
        "The Binding Revision number must be unique.",
    )
    _authorization_digest_uniq = models.Constraint(
        "UNIQUE(binding_id, authorization_digest)",
        "The authorization digest must be unique.",
    )

    def init(self):
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS inari_binding_site_device_capability_purpose_active_idx "
            "ON inari_device_binding_revision (company_id, site_id, device_id, capability_id, purpose) "
            "WHERE state = 'active'"
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if (
                record.binding_id.company_id != record.company_id
                or record.site_id != record.binding_id.site_id
            ):
                raise ValidationError(
                    _("The Binding Revision scope must match its Binding.")
                )
        return records

    def write(self, vals):
        immutable = {
            "revision_id",
            "binding_id",
            "company_id",
            "site_id",
            "device_id",
            "capability_id",
            "driver_profile_digest",
            "normalized_options",
            "authorization_digest",
            "revision_number",
        }
        if immutable.intersection(vals):
            raise ValidationError(_("A Binding Revision is immutable."))
        return super().write(vals)


class InariDeviceTestResult(models.Model):
    _name = "inari.device.test.result"
    _description = "Immutable Inari Device Test Result"
    _order = "finished_at desc, id desc"

    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict"
    )
    binding_revision_id = fields.Many2one(
        "inari.device.binding.revision",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    device_id = fields.Many2one(
        related="binding_revision_id.device_id", store=True, readonly=True
    )
    driver_profile_digest = fields.Char(required=True, readonly=True)
    pattern_version = fields.Char(required=True, readonly=True)
    result = fields.Selection(
        [
            ("passed", "Passed"),
            ("failed_environment", "Environment failed"),
            ("failed_contract", "Contract failed"),
            ("not_run", "Test did not run"),
        ],
        required=True,
        readonly=True,
    )
    checks = fields.Json(required=True, readonly=True)
    evidence = fields.Json(readonly=True)
    actor_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    started_at = fields.Datetime(required=True, readonly=True)
    finished_at = fields.Datetime(required=True, readonly=True)
    signed_result = fields.Text(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.binding_revision_id.company_id != record.company_id:
                raise ValidationError(
                    _("The Device Test company must match the Binding Revision.")
                )
            revision = record.binding_revision_id
            revision.latest_observed_test_id = record.id
            if record.result == "passed":
                revision.latest_passed_test_id = record.id
                revision.state = "tested"
            elif revision.state == "active" and record.result == "failed_contract":
                revision.state = "attention"
        return records

    def write(self, vals):
        raise ValidationError(_("A Device Test Result is immutable."))


class InariReportBinding(models.Model):
    _name = "inari.report.binding"
    _description = "Inari Report Binding"
    _order = "company_id, site_id, id"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        ondelete="restrict",
        index=True,
    )
    site_id = fields.Many2one(
        "inari.site", required=True, ondelete="restrict", index=True
    )
    report_action_id = fields.Many2one(
        "ir.actions.report", required=True, ondelete="restrict"
    )
    report_route = fields.Selection(
        [("manual", "Manual"), ("automatic", "Automatic")], required=True
    )
    purpose = fields.Selection(
        [("report_pdf", "Report PDF"), ("stock_label", "Stock label")], required=True
    )
    binding_revision_id = fields.Many2one(
        "inari.device.binding.revision", required=True, ondelete="restrict"
    )
    active = fields.Boolean(default=True, index=True)
    state = fields.Selection(
        [
            ("active", "Active"),
            ("attention", "Needs attention"),
            ("disabled", "Disabled"),
        ],
        default="active",
        index=True,
    )

    def init(self):
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS inari_report_binding_active_route_idx "
            "ON inari_report_binding (company_id, site_id, report_action_id, report_route) "
            "WHERE active"
        )

    @api.constrains("binding_revision_id", "purpose", "company_id", "site_id")
    def _check_revision(self):
        for record in self:
            revision = record.binding_revision_id
            if (
                revision.company_id != record.company_id
                or revision.site_id != record.site_id
            ):
                raise ValidationError(
                    _("The Report Binding scope must match its Binding Revision.")
                )
            if revision.binding_id.purpose != record.purpose:
                raise ValidationError(
                    _("The Report Binding Purpose must match its Device Binding.")
                )

    @api.model
    def resolve_for_route(self, company, site, report_action, route, purpose):
        binding = self.search(
            [
                ("company_id", "=", company.id),
                ("site_id", "=", site.id),
                ("report_action_id", "=", report_action.id),
                ("report_route", "=", route),
                ("purpose", "=", purpose),
                ("active", "=", True),
                ("state", "=", "active"),
            ],
            limit=1,
        )
        if not binding or binding.binding_revision_id.state != "active":
            raise UserError(
                _("No active Report Binding exists for this Site and route.")
            )
        return binding

    def action_disable(self):
        self.write({"active": False, "state": "disabled"})
        return True


class InariReportSequence(models.Model):
    _name = "inari.report.sequence"
    _description = "Inari report sequence summary"

    company_id = fields.Many2one(
        "res.company", required=True, ondelete="restrict", index=True
    )
    site_id = fields.Many2one(
        "inari.site", required=True, ondelete="restrict", index=True
    )
    state = fields.Selection(
        [("running", "Running"), ("complete", "Complete"), ("failed", "Failed")],
        default="running",
    )
    accepted_count = fields.Integer(default=0)
    pending_count = fields.Integer(default=0)
    failed_count = fields.Integer(default=0)
    message = fields.Char()
    document_ids = fields.One2many("inari.report.sequence.document", "sequence_id")

    def action_reconcile(self):
        for record in self:
            documents = record.document_ids
            record.write(
                {
                    "accepted_count": len(
                        documents.filtered(lambda doc: doc.state == "accepted")
                    ),
                    "pending_count": len(
                        documents.filtered(lambda doc: doc.state == "pending")
                    ),
                    "failed_count": len(
                        documents.filtered(lambda doc: doc.state == "failed")
                    ),
                    "state": "complete"
                    if not documents.filtered(lambda doc: doc.state == "pending")
                    else "running",
                }
            )
        return True


class InariReportSequenceDocument(models.Model):
    _name = "inari.report.sequence.document"
    _description = "Inari report sequence document"

    sequence_id = fields.Many2one(
        "inari.report.sequence", required=True, ondelete="cascade"
    )
    company_id = fields.Many2one(
        related="sequence_id.company_id",
        store=True,
        required=True,
        index=True,
        readonly=True,
    )
    source_model = fields.Char(required=True)
    source_res_id = fields.Integer(required=True)
    document_index = fields.Integer(required=True, default=0)
    state = fields.Selection(
        [("pending", "Pending"), ("accepted", "Accepted"), ("failed", "Failed")],
        default="pending",
        index=True,
    )
    print_intent_id = fields.Many2one("inari.print.intent", ondelete="restrict")
    error_code = fields.Char()
