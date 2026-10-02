import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


WORK_STATES = {
    "pending_agent": {"dispatching", "rejected", "canceled", "expired"},
    "dispatching": {"accepted", "rejected", "recovery_uncertain"},
    "recovery_uncertain": {"accepted", "canceled", "expired"},
    "accepted": set(),
    "rejected": set(),
    "canceled": set(),
    "expired": set(),
}


class InariPrintIntent(models.Model):
    _name = "inari.print.intent"
    _description = "Inari Print Intent"
    _order = "create_date desc, id desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict", index=True)
    site_id = fields.Many2one("inari.site", required=True, ondelete="restrict", index=True)
    binding_revision_id = fields.Many2one("inari.device.binding.revision", required=True, ondelete="restrict", index=True)
    device_id = fields.Many2one("inari.device", required=True, ondelete="restrict", index=True)
    agent_id = fields.Many2one("inari.agent", required=True, ondelete="restrict", index=True)
    origin_type = fields.Selection([("pos", "POS"), ("report", "Report"), ("preparation", "Preparation")], required=True)
    origin = fields.Json(required=True)
    document_kind = fields.Selection([("receipt_image", "Receipt image"), ("report_pdf", "Report PDF"), ("label_document", "Label document")], required=True)
    content_revision = fields.Char(required=True)
    copy_ordinal = fields.Integer(required=True, default=1)
    origin_submission_key = fields.Char(required=True, index=True)
    idempotency_key = fields.Char(required=True, index=True)
    payload_fingerprint = fields.Char(required=True, index=True)
    state = fields.Selection([("submission_pending", "Submission pending"), ("pending_agent", "Pending Agent"), ("accepted", "Accepted"), ("failed", "Failed"), ("outcome_unknown", "Outcome unknown"), ("resolved", "Resolved")], default="submission_pending", index=True)
    managed_work_id = fields.Many2one("inari.managed.work", ondelete="restrict")
    print_job_id = fields.Char(readonly=True)
    error_code = fields.Char()
    accepted_at = fields.Datetime(readonly=True)
    terminal_at = fields.Datetime(readonly=True)
    expires_at = fields.Datetime(readonly=True)
    retryable = fields.Boolean(default=False)
    contract_version = fields.Char(required=True)
    audit_ids = fields.One2many("inari.print.audit", "print_intent_id")

    _origin_key_uniq = models.Constraint(
        "UNIQUE(company_id, origin_submission_key)",
        "One Print Intent must use one Origin Submission Key.",
    )
    _idempotency_key_uniq = models.Constraint(
        "UNIQUE(company_id, idempotency_key)",
        "The Idempotency Key must be unique.",
    )
    _intent_copy_positive = models.Constraint(
        "CHECK(copy_ordinal > 0)",
        "The Copy Ordinal must be positive.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if any(scope.company_id != record.company_id for scope in (record.organization_id, record.site_id, record.binding_revision_id, record.device_id, record.agent_id)):
                raise ValidationError(_("The Print Intent scope must use one company."))
            if record.binding_revision_id.device_id != record.device_id:
                raise ValidationError(_("The Print Intent Device must match the Binding Revision."))
        return records

    @api.model
    def make_idempotency_key(self, origin, revision, device, copy_ordinal):
        canonical = json.dumps({"origin": origin, "revision": revision.authorization_digest, "device": device.device_id, "copy": copy_ordinal}, sort_keys=True, separators=(",", ":"))
        return "pi_v1_" + hashlib.sha256(canonical.encode()).hexdigest()

    @api.model
    def submit(self, values):
        """Create one intent, or return its exact idempotent existing record."""
        key = values.get("idempotency_key")
        fingerprint = values.get("payload_fingerprint")
        existing = self.search([("idempotency_key", "=", key)], limit=1)
        if existing:
            if existing.payload_fingerprint != fingerprint:
                raise ValidationError(_("The Payload Fingerprint does not match the Idempotency Key."))
            return existing
        return self.create(values)

    def mark_accepted(self, print_job_id, accepted_at=None):
        for record in self:
            if record.state in {"failed", "resolved"}:
                raise UserError(_("A terminal Print Intent cannot become accepted."))
            record.write({"state": "accepted", "print_job_id": print_job_id, "accepted_at": accepted_at or fields.Datetime.now(), "retryable": False})
        return True

    def mark_failed(self, error_code, retryable=False):
        self.write({"state": "failed", "error_code": error_code, "retryable": retryable, "terminal_at": fields.Datetime.now()})
        return True

    def action_resolve_state(self, state, error_code=None):
        allowed = {"accepted", "failed", "outcome_unknown", "expired", "canceled", "in_progress", "output_confirmed"}
        if state not in allowed:
            raise ValidationError(_("The Print Intent state is not valid."))
        self.write({"state": state, "error_code": error_code, "terminal_at": fields.Datetime.now() if state in {"failed", "outcome_unknown", "expired", "canceled", "output_confirmed"} else False})
        return True

    def action_reprint(self, reason, note=None):
        self.ensure_one()
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_manager"):
            raise AccessError(_("A Device Manager must approve a Reprint."))
        if reason == "other" and not note:
            raise ValidationError(_("The other Reprint reason requires a note."))
        binding = self.binding_revision_id.binding_id
        current = binding.active_revision_id
        if not current:
            raise UserError(_("The Binding has no active revision."))
        return self.env["inari.print.intent"].create({
            "company_id": self.company_id.id,
            "organization_id": self.organization_id.id,
            "site_id": self.site_id.id,
            "binding_revision_id": current.id,
            "device_id": current.device_id.id,
            "agent_id": current.device_id.agent_id.id,
            "origin_type": self.origin_type,
            "origin": self.origin,
            "document_kind": self.document_kind,
            "content_revision": self.content_revision,
            "copy_ordinal": self.copy_ordinal + 1,
            "origin_submission_key": "%s:reprint:%s" % (self.origin_submission_key, fields.Datetime.now()),
            "idempotency_key": "%s:reprint:%s" % (self.idempotency_key, fields.Datetime.now()),
            "payload_fingerprint": self.payload_fingerprint,
            "contract_version": self.contract_version,
            "state": "submission_pending",
        })


class InariPrintAudit(models.Model):
    _name = "inari.print.audit"
    _description = "Inari Print Audit Record"
    _order = "observed_at desc, id desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict", index=True)
    site_id = fields.Many2one("inari.site", required=True, ondelete="restrict", index=True)
    actor_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, ondelete="restrict")
    print_intent_id = fields.Many2one("inari.print.intent", required=True, ondelete="restrict", index=True)
    print_job_id = fields.Char(index=True)
    managed_work_id = fields.Char(index=True)
    device_id = fields.Many2one("inari.device", required=True, ondelete="restrict", index=True)
    binding_revision_id = fields.Many2one("inari.device.binding.revision", required=True, ondelete="restrict")
    origin_type = fields.Selection(related="print_intent_id.origin_type", store=True)
    action = fields.Selection([("submit", "Submit"), ("retry", "Retry"), ("reprint", "Reprint"), ("browser_print", "Browser print"), ("finish_without_ticket", "Finish without ticket"), ("state_update", "State update")], required=True)
    state = fields.Selection([("accepted", "Accepted"), ("in_progress", "In progress"), ("output_confirmed", "Output confirmed"), ("failed", "Failed"), ("outcome_unknown", "Outcome unknown"), ("expired", "Expired"), ("canceled", "Canceled")], required=True)
    reason = fields.Selection([("customer_request", "Customer request"), ("print_quality", "Print quality"), ("device_error", "Device error"), ("outcome_unknown_override", "Outcome unknown override"), ("preparation_recovery", "Preparation recovery"), ("other", "Other")])
    note = fields.Char()
    payload_fingerprint = fields.Char(required=True)
    payload_size = fields.Integer(required=True, default=0)
    contract_version = fields.Char(required=True)
    audit_event_id = fields.Char(index=True)
    envelope_id = fields.Char(index=True)
    observed_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    evidence = fields.Json()

    _audit_event_uniq = models.Constraint(
        "UNIQUE(company_id, audit_event_id)",
        "An audit event can be accepted once per company.",
    )
    _envelope_uniq = models.Constraint(
        "UNIQUE(company_id, envelope_id)",
        "An Agent envelope can be accepted once per company.",
    )

    @api.model
    def ingest_event(self, values):
        event_id = values.get("audit_event_id")
        if event_id:
            existing = self.search([("audit_event_id", "=", event_id)], limit=1)
            if existing:
                return existing
        return self.create(values)

    def action_finish_without_ticket(self, note):
        if not note:
            raise ValidationError(_("A reason is required when work finishes without a ticket."))
        for record in self:
            record.write({"action": "finish_without_ticket", "reason": "other", "note": note, "state": "failed"})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            intent = record.print_intent_id
            if intent.company_id != record.company_id or intent.device_id != record.device_id:
                raise ValidationError(_("The Print Audit scope must match the Print Intent."))
            if record.reason == "other" and not record.note:
                raise ValidationError(_("The other recovery reason requires a note."))
        return records


class InariRecoveryTask(models.Model):
    _name = "inari.recovery.task"
    _description = "Inari Print Recovery Task"
    _order = "priority desc, create_date"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    site_id = fields.Many2one("inari.site", required=True, ondelete="restrict")
    print_intent_id = fields.Many2one("inari.print.intent", required=True, ondelete="restrict", index=True)
    state = fields.Selection([("submission_pending", "Submission pending"), ("pending_agent", "Pending Agent"), ("accepted", "Accepted"), ("failed", "Failed"), ("resolved", "Resolved")], default="submission_pending", index=True)
    priority = fields.Selection([("0", "Normal"), ("1", "High")], default="0")
    error_code = fields.Char()
    summary = fields.Char()
    attempts = fields.Integer(default=0)
    resolved_at = fields.Datetime(readonly=True)

    def action_retry(self):
        for record in self:
            if record.state not in {"submission_pending", "pending_agent", "failed"}:
                raise UserError(_("Only unresolved recovery tasks can be retried."))
            record.write({"state": "submission_pending", "attempts": record.attempts + 1})
        return True

    def action_resolve(self, summary):
        if not summary:
            raise ValidationError(_("A recovery summary is required."))
        self.write({"state": "resolved", "summary": summary, "resolved_at": fields.Datetime.now()})
        return True
