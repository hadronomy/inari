from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestInariDevices(TransactionCase):
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
                    "controller_uuid": "org-test",
                    "name": "Test Organization",
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
                    "controller_uuid": "site-test",
                    "name": "Test Site",
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
                    "controller_uuid": "agent-test",
                    "agent_id": "agent-1",
                    "name": "Test Agent",
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
                    "controller_uuid": "device-test",
                    "device_id": "device-1",
                    "name": "Test Printer",
                    "organization_id": cls.organization.id,
                    "site_id": cls.site.id,
                    "agent_id": cls.agent.id,
                    "driver_profile_digest": "profile-1",
                },
                1,
            )
        )
        cls.capability = (
            cls.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": cls.company.id,
                    "controller_uuid": "capability-test",
                    "device_id": cls.device.id,
                    "operation": "report_pdf",
                    "contract_major": 1,
                },
                1,
            )
        )

    def test_binding_revision_requires_passed_test_before_activation(self):
        binding = (
            self.env["inari.device.binding"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "site_id": self.site.id,
                    "scope_type": "site",
                    "purpose": "report_pdf",
                }
            )
        )
        revision = binding.action_create_revision(self.device, self.capability)
        with self.assertRaises(UserError):
            binding.action_activate_revision(revision)
        test = (
            self.env["inari.device.test.result"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "binding_revision_id": revision.id,
                    "driver_profile_digest": "profile-1",
                    "pattern_version": "v1",
                    "result": "passed",
                    "checks": {"document": "correct"},
                    "actor_id": self.env.uid,
                    "started_at": fields.Datetime.now(),
                    "finished_at": fields.Datetime.now(),
                }
            )
        )
        self.assertEqual(revision.latest_passed_test_id, test)
        binding.action_activate_revision(revision)
        self.assertEqual(binding.active_revision_id, revision)

    def test_pos_receipt_binding_loads_an_exact_content_free_projection(self):
        config = self.env["pos.config"].create({"name": "Inari POS"})
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "receipt-capability-test",
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
                "driver_profile_digest": "profile-1",
                "pattern_version": "v1",
                "result": "passed",
                "checks": {"receipt": "correct"},
                "actor_id": self.env.uid,
                "started_at": fields.Datetime.now(),
                "finished_at": fields.Datetime.now(),
            }
        )
        binding.action_activate_revision(revision)
        origin = config.get_base_url().rstrip("/")
        self.env["inari.agent.endpoint"].sudo().create(
            {
                "company_id": self.company.id,
                "agent_id": self.agent.id,
                "origin": origin,
                "endpoint_url": "https://inari-agent.example",
                "certificate_fingerprint": "sha256:test",
            }
        )

        projection = config.inari_receipt_binding

        self.assertTrue(projection["authoritative"])
        self.assertEqual(projection["state"], "ready")
        self.assertEqual(projection["binding_revision_id"], revision.revision_id)
        self.assertEqual(projection["device_id"], "device-1")
        self.assertEqual(projection["agent_endpoint"], "https://inari-agent.example")
        self.assertNotIn("actor_id", projection)
        self.assertNotIn("client_grant", projection)

    def test_print_intent_idempotency_rejects_changed_fingerprint(self):
        binding = (
            self.env["inari.device.binding"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "site_id": self.site.id,
                    "scope_type": "site",
                    "purpose": "report_pdf",
                }
            )
        )
        revision = binding.action_create_revision(self.device, self.capability)
        values = {
            "company_id": self.company.id,
            "organization_id": self.organization.id,
            "site_id": self.site.id,
            "binding_revision_id": revision.id,
            "device_id": self.device.id,
            "agent_id": self.agent.id,
            "origin_type": "report",
            "origin": {"report_action": 1},
            "document_kind": "report_pdf",
            "content_revision": "content-1",
            "copy_ordinal": 1,
            "origin_submission_key": "origin-1",
            "idempotency_key": "pi-1",
            "payload_fingerprint": "fp-1",
            "contract_version": "inari.v1",
        }
        intent = self.env["inari.print.intent"].sudo().submit(values)
        self.assertEqual(self.env["inari.print.intent"].sudo().submit(values), intent)
        with self.assertRaises(ValidationError):
            self.env["inari.print.intent"].sudo().submit(
                dict(values, payload_fingerprint="fp-2")
            )

    def test_cursor_keeps_high_water_mark(self):
        cursor = (
            self.env["inari.reconciliation.cursor"]
            .sudo()
            .create(
                {
                    "company_id": self.company.id,
                    "organization_id": self.organization.id,
                    "cursor": "cursor-1",
                    "expires_at": fields.Datetime.now() + timedelta(days=90),
                }
            )
        )
        cursor.advance("cursor-2", 2)
        cursor.advance("cursor-old", 1)
        self.assertEqual(cursor.cursor, "cursor-2")
