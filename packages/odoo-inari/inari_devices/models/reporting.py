import hashlib
import re
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services.report_requests import (
    ReportRequestError,
    check_report_action,
    parse_report_request,
)


class InariReportBinding(models.Model):
    _inherit = "inari.report.binding"

    client_action_id = fields.Many2one(
        "ir.actions.client", readonly=True, copy=False, ondelete="set null"
    )

    def _validate_managed_request(self, action_id, values):
        request = parse_report_request(values)
        self.ensure_one()
        self.check_access("read")
        if not self.env.user.has_group("inari_devices.group_inari_operator"):
            raise AccessError(_("An Inari Operator is required to print this report."))
        if (
            type(action_id) is not int
            or self.id != request.binding_id
            or not self.client_action_id
            or self.client_action_id.id != action_id
            or self.company_id != self.env.company
        ):
            raise ReportRequestError(
                "The report action does not match its company and Binding."
            )
        action = self.client_action_id.sudo()
        check_report_action(
            request,
            {"type": action.type, "tag": action.tag, "context": action.context},
        )
        revision = self.binding_revision_id
        binding = revision.binding_id
        if (
            not self.active
            or self.state != "active"
            or revision.id != request.binding_revision_id
            or revision.state != "active"
            or not binding.active
            or binding.state != "active"
            or binding.active_revision_id != revision
            or self.site_id.id != request.site_id
        ):
            raise ReportRequestError(
                "The Report Binding requires a current active Binding Revision."
            )
        self._check_revision()
        device = revision.device_id.sudo()
        capability = revision.capability_id.sudo()
        operation = "report_pdf" if self.purpose == "report_pdf" else "label_document"
        if (
            not all(
                projection.active
                for projection in (
                    device,
                    device.site_id,
                    device.agent_id,
                    device.organization_id,
                    capability,
                )
            )
            or device.site_id != self.site_id
            or device.company_id != self.company_id
            or capability.device_id != device
            or capability.operation != operation
            or capability.contract_major != 1
            or revision.driver_profile_digest != device.driver_profile_digest
            or revision.latest_passed_test_id.result != "passed"
        ):
            raise ReportRequestError("The tested Device contract is no longer active.")
        report = self.report_action_id
        report.check_access("read")
        if (
            report.id != request.report_action_id
            or report.model != request.source_model
            or report.report_type != request.report_type
        ):
            raise ReportRequestError(
                "The source records do not match the bound report."
            )
        if report.group_ids and not report.group_ids & self.env.user.all_group_ids:
            raise AccessError(_("You do not have access to this report."))
        records = self.env[request.source_model].browse(request.source_ids)
        records.check_access("read")
        if records.exists().ids != list(request.source_ids):
            raise ReportRequestError(
                "One or more report source records no longer exist."
            )
        if "company_id" in records._fields and any(
            record.company_id and record.company_id != self.company_id
            for record in records
        ):
            raise ReportRequestError(
                "The report source records belong to another company."
            )
        self._check_report_site(records)
        return request, report, records

    def _check_report_site(self, records):
        self.ensure_one()
        if records._name == "pos.order":
            sites = records.config_id.inari_site_id
            if (
                any(not order.config_id.inari_site_id for order in records)
                or sites != self.site_id
            ):
                raise ReportRequestError(
                    "The POS orders do not resolve to this exact Site."
                )
        elif self.report_route != "manual":
            raise ReportRequestError(
                "This automatic report has no registered Site resolver."
            )

    @api.model
    def prepare_managed_report(self, action_id, values):
        try:
            request = parse_report_request(values)
            binding = self.browse(request.binding_id)
            if not binding.exists():
                raise ReportRequestError("The Report Binding no longer exists.")
            binding._validate_managed_request(action_id, values)
        except (ReportRequestError, AccessError, UserError):
            return {
                "version": 1,
                "handled": True,
                "status": "failed",
                "report_sequence_id": None,
                "documents": [],
                "message_key": "report.request_invalid",
            }
        ticket = secrets.token_urlsafe(32)
        sequence = (
            self.env["inari.report.sequence"]
            .sudo()
            .create(
                {
                    "company_id": binding.company_id.id,
                    "site_id": binding.site_id.id,
                    "actor_id": self.env.uid,
                    "source_action_id": action_id,
                    "report_binding_id": binding.id,
                    "request_values": values,
                    "ticket_digest": hashlib.sha256(ticket.encode("ascii")).hexdigest(),
                }
            )
        )
        return {
            "version": 1,
            "handled": True,
            "status": "prepared",
            "report_sequence_id": sequence.id,
            "action_ticket": ticket,
            "documents": [],
            "message_key": "report.prepared",
        }


class InariReportSequence(models.Model):
    _inherit = "inari.report.sequence"

    actor_id = fields.Many2one("res.users", readonly=True, ondelete="restrict")
    source_action_id = fields.Many2one(
        "ir.actions.client", readonly=True, ondelete="restrict"
    )
    report_binding_id = fields.Many2one(
        "inari.report.binding", readonly=True, ondelete="restrict"
    )
    request_values = fields.Json(readonly=True, groups="base.group_system")
    ticket_digest = fields.Char(
        readonly=True, copy=False, index=True, groups="base.group_system"
    )

    _ticket_unique = models.Constraint(
        "UNIQUE(ticket_digest)", "A report action ticket must be unique."
    )

    def _for_action_ticket(self, ticket):
        if not isinstance(ticket, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{43}", ticket
        ):
            raise ReportRequestError("The report action ticket is invalid.")
        if not self.env.user.has_group("inari_devices.group_inari_operator"):
            raise AccessError(_("An Inari Operator is required to print this report."))
        sequence = self.sudo().search(
            [
                ("company_id", "=", self.env.company.id),
                ("actor_id", "=", self.env.uid),
                (
                    "ticket_digest",
                    "=",
                    hashlib.sha256(ticket.encode("ascii")).hexdigest(),
                ),
            ],
            limit=1,
        )
        if not sequence:
            raise ReportRequestError("The report action ticket is invalid.")
        return sequence


class PosConfig(models.Model):
    _inherit = "pos.config"

    inari_site_id = fields.Many2one(
        "inari.site", check_company=True, ondelete="restrict"
    )
