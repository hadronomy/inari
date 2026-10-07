import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services.http_client import RemoteServiceError
from ..services.managed_work import build_managed_work_client


_logger = logging.getLogger(__name__)


def _require_admin(env):
    if not env.is_superuser() and not env.user.has_group(
        "inari_devices.group_inari_admin"
    ):
        raise AccessError(
            _("A System Administrator is required to connect the Controller.")
        )


class InariInventorySetup(models.Model):
    _inherit = "inari.setup.state"

    last_inventory_at = fields.Datetime(readonly=True)
    device_count = fields.Integer(compute="_compute_device_count")

    def _compute_device_count(self):
        for setup in self:
            setup.device_count = self.env["inari.device"].search_count(
                [("company_id", "=", setup.company_id.id)]
            )

    def action_connect(self):
        _require_admin(self.env)
        return {
            "type": "ir.actions.act_window",
            "name": _("Connect Inari"),
            "res_model": "inari.connection.wizard",
            "view_mode": "form",
            "target": "new",
        }

    def action_sync_inventory(self):
        _require_admin(self.env)
        for setup in self:
            if setup.company_id not in self.env.companies:
                raise AccessError(_("The Inari connection belongs to another company."))
            try:
                setup._sync_inventory()
            except RemoteServiceError as error:
                raise UserError(
                    _(
                        "The Controller inventory could not be synchronized: %s",
                        str(error),
                    )
                ) from None
        return {"type": "ir.actions.client", "tag": "reload"}

    def _sync_inventory(self):
        self.ensure_one()
        if self.state == "decommissioned":
            raise UserError(_("The Inari connection is decommissioned."))
        identity = (
            self.env["inari.workload.identity"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company_id.id),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
        )
        if not identity:
            raise UserError(_("Connect an Organization Workload Identity first."))
        with identity._managed_work_client() as client:
            snapshot = client.inventory()
        self._apply_inventory(snapshot, identity.organization_id.controller_uuid)

    def _apply_inventory(self, snapshot, organization_uuid):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM inari_setup_state WHERE id = %s FOR UPDATE", [self.id]
        )
        self.invalidate_recordset(["last_inventory_at", "state"])
        if self.state == "decommissioned":
            raise UserError(_("The Inari connection is decommissioned."))
        if self.last_inventory_at and snapshot.observed_at < self.last_inventory_at:
            raise ValidationError(
                _("A newer Controller inventory is already synchronized. Try again.")
            )
        company_id = self.company_id.id
        env = self.sudo().with_company(self.company_id).env
        organizations = env["inari.organization"].with_context(active_test=False)
        existing = organizations.search([("company_id", "=", company_id)], limit=1)
        if existing and existing.controller_uuid != organization_uuid:
            raise ValidationError(
                _("This company is connected to another Organization.")
            )
        organization = organizations.sync_values(
            {
                "company_id": company_id,
                "controller_uuid": organization_uuid,
                "name": snapshot.organization_name,
                "active": True,
            }
        )
        scope = {
            "company_id": company_id,
            "organization_id": organization.id,
            "active": True,
        }
        sites = {}
        agents = {}
        present = {"inari.site": [], "inari.agent": [], "inari.device": []}
        for item in snapshot.sites:
            record = (
                env["inari.site"]
                .with_context(active_test=False)
                .sync_values(
                    {
                        **scope,
                        "controller_uuid": item.site_id,
                        "name": item.name,
                        "code": item.site_id,
                    }
                )
            )
            sites[item.site_id] = record
            present["inari.site"].append(record.id)
        for item in snapshot.agents:
            record = (
                env["inari.agent"]
                .with_context(active_test=False)
                .sync_values(
                    {
                        **scope,
                        "controller_uuid": item.agent_id,
                        "agent_id": item.agent_id,
                        "name": item.agent_id,
                        "site_id": sites[item.site_id].id,
                        "connection_state": item.state,
                        "last_seen_at": item.last_seen_at,
                    }
                )
            )
            agents[item.agent_id] = record
            present["inari.agent"].append(record.id)
        for item in snapshot.devices:
            record = (
                env["inari.device"]
                .with_context(active_test=False)
                .sync_values(
                    {
                        **scope,
                        "controller_uuid": f"{item.agent_id}/{item.device_id}",
                        "device_id": item.device_id,
                        "name": item.name,
                        "agent_id": agents[item.agent_id].id,
                        "site_id": sites[item.site_id].id,
                        "kind": item.kind,
                        "device_class": item.device_class,
                        "transport": item.transport,
                        "connection_state": item.state
                        if agents[item.agent_id].connection_state == "online"
                        else "offline",
                        "last_observed_at": item.last_seen_at,
                    }
                )
            )
            present["inari.device"].append(record.id)
        for model, ids in present.items():
            env[model].search(
                [
                    ("company_id", "=", company_id),
                    ("organization_id", "=", organization.id),
                    ("id", "not in", ids),
                ]
            ).archive_absent()
        self.sudo().write(
            {
                "last_inventory_at": snapshot.observed_at,
                "last_check_at": fields.Datetime.now(),
                "last_error": False,
                "state": "ready",
            }
        )
        return organization

    @api.model
    def _cron_sync_inventory(self):
        for setup in self.sudo().search(
            [("controller_url", "!=", False), ("state", "!=", "decommissioned")]
        ):
            try:
                with self.env.cr.savepoint():
                    setup._sync_inventory()
            except (RemoteServiceError, UserError, ValidationError):
                _logger.warning(
                    "Inari inventory synchronization failed for company %s",
                    setup.company_id.id,
                )
                setup.write(
                    {
                        "state": "blocked",
                        "last_check_at": fields.Datetime.now(),
                        "last_error": "Controller inventory synchronization failed. Check the connection and workload identity.",
                    }
                )
        return True


class InariConnectionWizard(models.TransientModel):
    _name = "inari.connection.wizard"
    _description = "Connect an Inari Organization"

    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company
    )
    controller_url = fields.Char(required=True, string="Controller URL")
    organization_uuid = fields.Char(required=True, string="Organization identity")
    issuer_url = fields.Char(required=True, string="Identity issuer URL")
    client_id = fields.Char(required=True, string="Workload client identity")

    def action_connect(self):
        self.ensure_one()
        _require_admin(self.env)
        if self.company_id not in self.env.companies:
            raise AccessError(_("Select an allowed company."))
        try:
            with build_managed_work_client(
                database=self.env.cr.dbname,
                company_id=self.company_id.id,
                organization_id=self.organization_uuid,
                client_id=self.client_id,
                issuer=self.issuer_url,
                controller=self.controller_url,
            ) as client:
                snapshot = client.inventory()
        except RemoteServiceError as error:
            raise UserError(
                _("The Controller connection failed: %s", str(error))
            ) from None
        setup_model = self.env["inari.setup.state"]
        setup = setup_model.search([("company_id", "=", self.company_id.id)], limit=1)
        if setup.state == "decommissioned":
            raise UserError(_("The Inari connection is decommissioned."))
        if not setup:
            setup = setup_model.create(
                {
                    "company_id": self.company_id.id,
                    "controller_url": self.controller_url,
                }
            )
        organization = setup._apply_inventory(snapshot, self.organization_uuid)
        identity_model = self.env["inari.workload.identity"]
        identity = identity_model.search(
            [("company_id", "=", self.company_id.id)], limit=1
        )
        values = {
            "company_id": self.company_id.id,
            "organization_id": organization.id,
            "client_id": self.client_id,
            "issuer_url": self.issuer_url,
            "state": "active",
        }
        if identity:
            identity.write(values)
        else:
            identity_model.create(values)
        setup.write({"controller_url": self.controller_url})
        return {"type": "ir.actions.client", "tag": "reload"}


class InariInventoryDevice(models.Model):
    _inherit = "inari.device"

    def action_connect(self):
        return self.env["inari.setup.state"].action_connect()

    def action_sync_inventory(self):
        _require_admin(self.env)
        setup = self.env["inari.setup.state"].search(
            [("company_id", "=", self.env.company.id)], limit=1
        )
        if not setup:
            return self.action_connect()
        return setup.action_sync_inventory()
