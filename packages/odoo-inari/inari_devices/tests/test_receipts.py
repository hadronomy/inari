from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestPrinterReceipts(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.organization = (
            cls.env["inari.organization"]
            .sudo()
            .sync_values(
                {
                    "company_id": cls.company.id,
                    "controller_uuid": "receipt-org",
                    "name": "Organization",
                },
                1,
            )
        )
        cls.site = (
            cls.env["inari.site"]
            .sudo()
            .sync_values(
                {
                    "company_id": cls.company.id,
                    "controller_uuid": "receipt-site",
                    "name": "Site",
                    "organization_id": cls.organization.id,
                },
                1,
            )
        )
        cls.agent = (
            cls.env["inari.agent"]
            .sudo()
            .sync_values(
                {
                    "company_id": cls.company.id,
                    "controller_uuid": "receipt-agent",
                    "agent_id": "receipt-agent",
                    "name": "Agent",
                    "organization_id": cls.organization.id,
                    "site_id": cls.site.id,
                },
                1,
            )
        )
        cls.device = (
            cls.env["inari.device"]
            .sudo()
            .sync_values(
                {
                    "company_id": cls.company.id,
                    "controller_uuid": "receipt-printer",
                    "device_id": "receipt-printer",
                    "name": "Printer",
                    "kind": "printer",
                    "organization_id": cls.organization.id,
                    "site_id": cls.site.id,
                    "agent_id": cls.agent.id,
                    "driver_profile_digest": "receipt-profile",
                },
                1,
            )
        )
        cls.manager = new_test_user(
            cls.env,
            login="receipt-manager",
            groups="base.group_user,inari_devices.group_inari_manager,point_of_sale.group_pos_user",
        )

    def test_unbound_printer_explains_setup_without_authorization(self):
        options = (
            self.env["inari.device"]
            .with_user(self.manager)
            .get_test_receipt_options(self.device.id)
        )
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["channels"], [])
        self.assertTrue(options[0]["unavailable_reason"])

    def test_operator_cannot_open_or_read_test_receipt_authorization(self):
        operator = new_test_user(
            self.env,
            login="receipt-operator",
            groups="base.group_user,inari_devices.group_inari_operator",
        )
        with self.assertRaises(AccessError):
            self.device.with_user(operator).action_test_receipts()
        with self.assertRaises(AccessError):
            self.env["inari.device"].with_user(operator).get_test_receipt_options()

    def test_non_printer_cannot_open_test_receipts(self):
        self.device.sudo().write({"kind": "scale"})
        with self.assertRaises(UserError):
            self.device.with_user(self.manager).action_test_receipts()

    def test_printers_outside_the_selected_company_are_not_read(self):
        other = self.env["res.company"].create({"name": "Other receipt company"})
        manager = new_test_user(
            self.env,
            login="receipt-other-manager",
            groups="base.group_user,inari_devices.group_inari_manager",
            company_id=other.id,
            company_ids=[(6, 0, [other.id])],
        )
        devices = (
            self.env["inari.device"]
            .with_user(manager)
            .with_context(allowed_company_ids=[other.id])
        )
        self.assertFalse(devices.get_test_receipt_options())
        with self.assertRaises(AccessError):
            devices.get_test_receipt_options(self.device.id)

    def test_active_binding_uses_exact_session_and_printer(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example"
        )
        config = self.env["pos.config"].create({"name": "Receipt POS"})
        session = (
            self.env["pos.session"]
            .sudo()
            .create({"config_id": config.id, "state": "opened"})
        )
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "receipt-capability",
                    "device_id": self.device.id,
                    "operation": "receipt_image",
                    "contract_major": 1,
                },
                1,
            )
        )
        binding = (
            self.env["inari.device.binding"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "site_id": self.site.id,
                    "scope_type": "pos_config",
                    "purpose": "pos_receipt",
                    "pos_config_id": config.id,
                }
            )
        )
        revision = binding.action_create_revision(self.device, capability)
        self.env["inari.device.test.result"].sudo().create(
            {
                "company_id": self.company.id,
                "binding_revision_id": revision.id,
                "driver_profile_digest": "receipt-profile",
                "pattern_version": "v1",
                "result": "passed",
                "checks": {"receipt": "correct"},
                "actor_id": self.env.uid,
                "started_at": fields.Datetime.now(),
                "finished_at": fields.Datetime.now(),
            }
        )
        binding.action_activate_revision(revision)
        self.env["inari.agent.endpoint"].sudo().create(
            {
                "company_id": self.company.id,
                "agent_id": self.agent.id,
                "origin": "https://odoo.example",
                "endpoint_url": "https://agent.example",
                "certificate_fingerprint": "sha256:receipt",
            }
        )
        options = (
            self.env["inari.device"]
            .with_user(self.manager)
            .get_test_receipt_options(self.device.id)
        )
        channel = options[0]["channels"][0]
        self.assertEqual(channel["pos_session_id"], str(session.id))
        self.assertEqual(channel["binding"]["device_id"], self.device.device_id)
        self.assertEqual(
            channel["binding"]["binding_revision_id"], revision.revision_id
        )
        session.sudo().write({"state": "closed"})
        self.assertFalse(
            self.env["inari.device"].get_test_receipt_options(self.device.id)[0][
                "channels"
            ]
        )
