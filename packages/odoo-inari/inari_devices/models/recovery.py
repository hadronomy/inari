from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class InariWebhookDelivery(models.Model):
    _name = "inari.webhook.delivery"
    _description = "Signed Inari Webhook Delivery"
    _order = "received_at desc"

    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", ondelete="restrict", index=True)
    delivery_id = fields.Char(required=True, index=True)
    attempt = fields.Integer(required=True, default=1)
    kid = fields.Char(required=True)
    signature = fields.Text(required=True)
    content_digest = fields.Char(required=True)
    received_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    issued_at = fields.Datetime()
    state = fields.Selection([("received", "Received"), ("processed", "Processed"), ("replayed", "Replayed"), ("rejected", "Rejected")], default="received", index=True)
    error_code = fields.Char()
    payload = fields.Json(required=True)
    processed = fields.Boolean(compute="_compute_processed")

    _delivery_attempt_uniq = models.Constraint(
        "UNIQUE(delivery_id, attempt)",
        "A webhook attempt can be stored once.",
    )

    def _compute_processed(self):
        for record in self:
            record.processed = record.state == "processed"

    @api.model
    def receive_signed(self, payload, headers):
        delivery_id = headers.get("X-Inari-Delivery-Id") or payload.get("delivery_id")
        if not delivery_id:
            raise ValidationError(_("The webhook has no delivery identity."))
        attempt = int(headers.get("X-Inari-Attempt", payload.get("attempt", 1)))
        existing = self.search([("delivery_id", "=", delivery_id)], order="id desc", limit=1)
        if existing:
            existing.write({"state": "replayed"})
            return existing
        organization = self.env["inari.organization"].search([
            "|", ("id", "=", payload.get("organization_id")),
            ("controller_uuid", "=", payload.get("organization_uuid"))
        ], limit=1) if payload.get("organization_id") or payload.get("organization_uuid") else self.env["inari.organization"]
        company_id = payload.get("company_id") or (organization.company_id.id if organization else False)
        if not company_id:
            raise ValidationError(_("The webhook has no company scope."))
        delivery = self.create({
            "company_id": company_id,
            "organization_id": organization.id if organization else payload.get("organization_id"),
            "delivery_id": delivery_id,
            "attempt": attempt,
            "kid": headers.get("X-Inari-Key-Id", payload.get("kid", "unknown")),
            "signature": headers.get("Signature", ""),
            "content_digest": headers.get("Content-Digest", ""),
            "payload": payload,
            "state": "received",
        })
        delivery._process_payload()
        return delivery

    def _process_payload(self):
        for delivery in self:
            event = delivery.payload.get("event", delivery.payload)
            intent = self.env["inari.print.intent"].search([("print_job_id", "=", event.get("print_job_id"))], limit=1)
            if intent and event.get("state") == "accepted":
                intent.mark_accepted(event.get("print_job_id"), event.get("accepted_at"))
            elif intent and event.get("state") in {"failed", "expired", "canceled"}:
                intent.mark_failed(event.get("error_code", event.get("state")))
            delivery.state = "processed"
        return True

    @api.model
    def purge_expired(self):
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=90)
        self.search([("received_at", "<", cutoff)]).unlink()
        return True


class InariReconciliationCursor(models.Model):
    _name = "inari.reconciliation.cursor"
    _description = "Organization Reconciliation Cursor"

    company_id = fields.Many2one("res.company", required=True, ondelete="restrict", index=True)
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict", index=True)
    cursor = fields.Char(required=True)
    resource_version = fields.Integer(default=0)
    state = fields.Selection([("active", "Active"), ("expired", "Expired")], default="active", index=True)
    expires_at = fields.Datetime(required=True)
    last_reconciled_at = fields.Datetime()

    _cursor_org_uniq = models.Constraint(
        "UNIQUE(company_id, organization_id)",
        "An Organization has one reconciliation cursor.",
    )

    def advance(self, cursor, resource_version):
        for record in self:
            if record.state == "expired" or record.expires_at < fields.Datetime.now():
                record.state = "expired"
                raise UserError(_("The cursor expired. Run bounded full reconciliation."))
            if resource_version >= record.resource_version:
                record.write({"cursor": cursor, "resource_version": resource_version, "last_reconciled_at": fields.Datetime.now()})
        return True

    def action_expire(self):
        self.write({"state": "expired"})
        return True

    @api.model
    def cron_reconcile(self):
        for cursor in self.search([("state", "=", "active")]):
            now = fields.Datetime.now()
            if cursor.expires_at < now:
                cursor.write({"state": "expired", "last_reconciled_at": now})
            else:
                cursor.write({"last_reconciled_at": now})
        return True


class InariDecommissionRun(models.Model):
    _name = "inari.decommission.run"
    _description = "Scoped Inari Decommission Run"
    _order = "create_date desc"

    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, ondelete="restrict", index=True)
    scope = fields.Selection([("site", "Site"), ("organization", "Organization"), ("database", "Database")], required=True)
    site_id = fields.Many2one("inari.site", ondelete="restrict")
    organization_id = fields.Many2one("inari.organization", ondelete="restrict")
    phase = fields.Selection([("block", "Block work"), ("drain", "Drain work"), ("audit", "Check audit"), ("revoke", "Revoke authority"), ("purge", "Purge spool"), ("deactivate", "Deactivate bindings"), ("complete", "Complete"), ("failed", "Failed")], default="block", index=True)
    actor_id = fields.Many2one("res.users", required=True, default=lambda self: self.env.user, ondelete="restrict")
    started_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    completed_at = fields.Datetime(readonly=True)
    result = fields.Text()
    failure_code = fields.Char()
    retry_count = fields.Integer(default=0)
    evidence_ids = fields.One2many("inari.decommission.evidence", "run_id")

    def _check_manager(self):
        if not self.env.is_superuser() and not self.env.user.has_group("inari_devices.group_inari_manager"):
            raise AccessError(_("A Device Manager is required for decommission."))

    def action_advance(self):
        self._check_manager()
        order = ["block", "drain", "audit", "revoke", "purge", "deactivate", "complete"]
        for record in self:
            if record.phase in {"complete", "failed"}:
                raise UserError(_("This Decommission Run is not active."))
            next_phase = order[order.index(record.phase) + 1]
            vals = {"phase": next_phase}
            if next_phase == "complete":
                vals.update(completed_at=fields.Datetime.now(), result="completed")
            record.write(vals)
        return True

    def action_retry(self):
        self._check_manager()
        for record in self:
            record.write({"phase": "block", "retry_count": record.retry_count + 1, "failure_code": False})
        return True


class InariDecommissionEvidence(models.Model):
    _name = "inari.decommission.evidence"
    _description = "Decommission evidence"

    run_id = fields.Many2one("inari.decommission.run", required=True, ondelete="cascade")
    company_id = fields.Many2one(related="run_id.company_id", store=True, required=True, index=True, readonly=True)
    evidence_type = fields.Selection([("absence", "Agent absence"), ("inventory", "Inventory"), ("revocation", "Revocation"), ("duplicate_risk", "Duplicate output risk")], required=True)
    identity = fields.Char(required=True)
    details = fields.Text(required=True)


class InariDecommissionPurgeAuthority(models.Model):
    _name = "inari.decommission.purge.authority"
    _description = "Short-lived Device Spool purge authority"

    run_id = fields.Many2one("inari.decommission.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="run_id.company_id", store=True, index=True, readonly=True)
    agent_id = fields.Many2one("inari.agent", required=True, ondelete="restrict")
    organization_id = fields.Many2one("inari.organization", required=True, ondelete="restrict")
    site_id = fields.Many2one("inari.site", required=True, ondelete="restrict")
    authority_id = fields.Char(required=True, index=True)
    issued_at = fields.Datetime(required=True, default=fields.Datetime.now)
    expires_at = fields.Datetime(required=True)
    state = fields.Selection([("active", "Active"), ("used", "Used"), ("expired", "Expired"), ("revoked", "Revoked")], default="active", index=True)

    _authority_id_uniq = models.Constraint(
        "UNIQUE(authority_id)",
        "The purge authority identity must be unique.",
    )

    def action_use(self):
        for record in self:
            if record.state != "active" or record.expires_at < fields.Datetime.now():
                record.state = "expired"
                raise UserError(_("The purge authority is not active."))
            record.state = "used"
        return True
