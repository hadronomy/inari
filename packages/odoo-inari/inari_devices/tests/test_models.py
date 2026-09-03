import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..services import PairingAssertionSigner, SignedPairingAssertion


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

    def test_preparation_printer_loads_its_exact_local_binding(self):
        config = self.env["pos.config"].create({"name": "Inari Kitchen POS"})
        printer = self.env["pos.printer"].create(
            {
                "name": "Kitchen",
                "pos_config_ids": [(6, 0, [config.id])],
            }
        )
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "preparation-capability-test",
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
                    "purpose": "pos_preparation",
                    "pos_config_id": config.id,
                    "pos_printer_id": printer.id,
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
                "checks": {"preparation": "correct"},
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
                "endpoint_url": "https://kitchen-agent.example",
                "certificate_fingerprint": "sha256:kitchen",
            }
        )

        [loaded] = self.env["pos.printer"]._load_pos_data_read(printer, config)
        projection = loaded["inari_preparation_binding"]
        scope = self.env["inari.pairing.assertion"]._local_device_scope(
            config, "agent-1"
        )

        self.assertTrue(projection["authoritative"])
        self.assertEqual(projection["binding_revision_id"], revision.revision_id)
        self.assertEqual(projection["device_id"], "device-1")
        self.assertEqual(projection["agent_endpoint"], "https://kitchen-agent.example")
        self.assertEqual(scope["binding"], binding)

    def test_pos_cash_drawer_projects_only_its_required_permissions(self):
        config = self.env["pos.config"].create({"name": "Inari Drawer POS"})
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "drawer-capability-test",
                    "device_id": self.device.id,
                    "operation": "open_cash_drawer",
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
                    "purpose": "pos_cash_drawer",
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
                "checks": {"drawer": "opened"},
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
                "endpoint_url": "https://drawer-agent.example",
                "certificate_fingerprint": "sha256:drawer",
            }
        )

        projection = config.inari_cash_drawer_binding

        self.assertEqual(projection["purpose"], "pos_cash_drawer")
        self.assertEqual(projection["device_id"], "device-1")
        self.assertEqual(
            projection["requested_permissions"],
            ["device_work:drawer", "jobs:read"],
        )

    def test_certified_scale_projection_enables_the_native_scale_screen(self):
        config = self.env["pos.config"].create({"name": "Inari Scale POS"})
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "scale-capability-test",
                    "device_id": self.device.id,
                    "operation": "scale_reading",
                    "contract_major": 1,
                    "certification_ref": "certification-1",
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
                    "purpose": "pos_scale",
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
                "checks": {"scale": "certified"},
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
                "endpoint_url": "https://scale-agent.example",
                "certificate_fingerprint": "sha256:scale",
            }
        )

        projection = config.inari_scale_binding

        self.assertTrue(config.iface_electronic_scale)
        self.assertEqual(projection["state"], "ready")
        self.assertEqual(projection["certification_id"], "certification-1")
        self.assertEqual(
            projection["requested_permissions"],
            ["device_read:scale", "events:read"],
        )

    def test_pairing_assertion_is_exact_and_idempotent(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.example"
        )
        config = self.env["pos.config"].create({"name": "Pairing POS"})
        session = self.env["pos.session"].sudo().create({"config_id": config.id})
        capability = (
            self.env["inari.device.capability"]
            .sudo()
            .sync_values(
                {
                    "company_id": self.company.id,
                    "controller_uuid": "pairing-receipt-capability",
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
        self.env["inari.agent.endpoint"].sudo().create(
            {
                "company_id": self.company.id,
                "agent_id": self.agent.id,
                "origin": "https://odoo.example",
                "endpoint_url": "https://agent.example",
                "certificate_fingerprint": "sha256:pairing",
            }
        )
        request_values = {
            "request_id": "pairing_request-1",
            "agent_id": "agent-1",
            "browser_origin": "https://odoo.example",
            "agent_endpoint": "https://agent.example",
            "database": self.env.cr.dbname,
            "company_id": str(self.company.id),
            "organization_id": "org-test",
            "site_id": "site-test",
            "pos_configuration_id": str(config.id),
            "audience": "inari-agent",
            "browser_jwk_thumbprint": "A" * 43,
            "requested_permissions": ["device_work:receipt_image", "jobs:read"],
            "session_nonce": "session_nonce-1",
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10))
            .isoformat()
            .replace("+00:00", "Z"),
            "state": "approved",
        }
        signer = type(
            "Signer",
            (),
            {
                "sign": lambda _, claims: SignedPairingAssertion(
                    compact_jws="header.payload.signature",
                    signer_key_id="inari-odoo-pairing-test:v2",
                )
            },
        )()

        with patch(
            "odoo.addons.inari_devices.models.pairing.build_pairing_assertion_signer",
            return_value=signer,
        ) as signer_factory:
            first = (
                self.env["inari.pairing.assertion"]
                .sudo()
                .issue_for_pos(request_values, session.id)
            )
            second = (
                self.env["inari.pairing.assertion"]
                .sudo()
                .issue_for_pos(request_values, session.id)
            )

        self.assertEqual(first, second)
        self.assertEqual(first["assertion"], "header.payload.signature")
        self.assertEqual(signer_factory.call_count, 1)
        record = self.env["inari.pairing.assertion"].sudo().search([])
        self.assertEqual(len(record), 1)
        self.assertEqual(record.pairing_request_id, "pairing_request-1")
        self.assertEqual(record.pos_session_id, session)

    def test_transit_signer_binds_the_reported_key_version(self):
        calls = []

        class Client:
            def request(self, method, path, **values):
                calls.append((method, path, values))
                if method == "GET":
                    return {
                        "data": {
                            "latest_version": 2,
                            "type": "ed25519",
                            "supports_signing": True,
                            "keys": {"2": {"public_key": "public-key"}},
                        }
                    }
                return {
                    "data": {
                        "signature": "vault:v2:"
                        + base64.b64encode(bytes(range(64))).decode()
                    }
                }

        signer = PairingAssertionSigner(
            client=Client(),
            token_provider=type("Tokens", (), {"token": lambda _: "short-token"})(),
            transit_mount="transit",
            key_name="inari-odoo-pairing-test-1",
        )

        signed = signer.sign({"sub": "pairing_request-1"})

        header_segment, payload_segment, signature_segment = signed.compact_jws.split(
            "."
        )
        header = json.loads(
            base64.urlsafe_b64decode(header_segment + "=" * (-len(header_segment) % 4))
        )
        payload = json.loads(
            base64.urlsafe_b64decode(
                payload_segment + "=" * (-len(payload_segment) % 4)
            )
        )
        self.assertEqual(header["alg"], "Ed25519")
        self.assertEqual(header["kid"], "inari-odoo-pairing-test-1:v2")
        self.assertEqual(payload, {"sub": "pairing_request-1"})
        self.assertEqual(len(base64.urlsafe_b64decode(signature_segment + "==")), 64)
        self.assertEqual(calls[1][2]["json_body"]["key_version"], 2)
        self.assertEqual(
            base64.b64decode(calls[1][2]["json_body"]["input"]).decode(),
            f"{header_segment}.{payload_segment}",
        )

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
