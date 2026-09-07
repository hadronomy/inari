from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, tagged

from ..services.report_requests import ReportRequestError


@tagged("post_install", "-at_install")
class TestInariReportRequests(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids += cls.env.ref("inari_devices.group_inari_manager")
        company = cls.env.company

        def projection(model, key, **values):
            return (
                cls.env[model]
                .sudo()
                .create({"company_id": company.id, "controller_uuid": key, **values})
            )

        organization = projection("inari.organization", "org-reports", name="Reports")
        cls.site = projection(
            "inari.site",
            "site-reports",
            name="Reports",
            organization_id=organization.id,
        )
        agent = projection(
            "inari.agent",
            "agent-reports",
            name="Reports",
            agent_id="agt_reports",
            organization_id=organization.id,
            site_id=cls.site.id,
        )
        device = projection(
            "inari.device",
            "device-reports",
            name="Reports",
            device_id="dev_reports",
            organization_id=organization.id,
            site_id=cls.site.id,
            agent_id=agent.id,
            driver_profile_digest="profile-reports",
        )
        capability = projection(
            "inari.device.capability",
            "capability-reports",
            device_id=device.id,
            operation="report_pdf",
            contract_major=1,
        )
        cls.device_binding = cls.env["inari.device.binding"].create(
            {
                "company_id": company.id,
                "site_id": cls.site.id,
                "scope_type": "site",
                "purpose": "report_pdf",
            }
        )
        cls.revision = cls.device_binding.action_create_revision(device, capability)
        cls.env["inari.device.test.result"].sudo().create(
            {
                "company_id": company.id,
                "binding_revision_id": cls.revision.id,
                "driver_profile_digest": "profile-reports",
                "pattern_version": "v1",
                "result": "passed",
                "checks": {"document": "correct"},
                "actor_id": cls.env.uid,
                "started_at": fields.Datetime.now(),
                "finished_at": fields.Datetime.now(),
            }
        )
        cls.device_binding.action_activate_revision(cls.revision)
        cls.report = cls.env["ir.actions.report"].create(
            {
                "name": "Report request test",
                "model": "res.partner",
                "report_type": "qweb-pdf",
                "report_name": "inari_devices.request_test",
            }
        )
        cls.binding = cls.env["inari.report.binding"].create(
            {
                "company_id": company.id,
                "site_id": cls.site.id,
                "report_action_id": cls.report.id,
                "report_route": "manual",
                "purpose": "report_pdf",
                "binding_revision_id": cls.revision.id,
            }
        )
        cls.action = cls.env["ir.actions.client"].create(
            {
                "name": "Print with Inari",
                "tag": "inari_report_print",
                "context": repr(
                    {
                        "inari_managed_report": True,
                        "inari_report_binding_id": cls.binding.id,
                        "inari_binding_revision_id": cls.revision.id,
                    }
                ),
            }
        )
        cls.binding.client_action_id = cls.action
        cls.partner = cls.env["res.partner"].create(
            {"name": "Report source", "company_id": company.id}
        )

    def _request(self, **changes):
        return {
            "version": 1,
            "binding_id": self.binding.id,
            "binding_revision_id": self.revision.id,
            "report_action_id": self.report.id,
            "source_model": "res.partner",
            "source_ids": self.partner.ids,
            "site_id": self.site.id,
            "report_type": "qweb-pdf",
            "copies": 1,
            "wizard_data": {},
            **changes,
        }

    def test_preparation_creates_no_print_intent(self):
        Intent = self.env["inari.print.intent"]
        before = Intent.search_count([])
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "prepared")
        sequence = self.env["inari.report.sequence"].browse(
            result["report_sequence_id"]
        )
        self.assertEqual(sequence.actor_id, self.env.user)
        self.assertEqual(sequence.request_values["source_ids"], self.partner.ids)
        self.assertNotEqual(sequence.ticket_digest, result["action_ticket"])
        self.assertEqual(Intent.search_count([]), before)

    def test_browser_authority_fails_before_sequence_creation(self):
        Sequence = self.env["inari.report.sequence"]
        before = Sequence.search_count([])
        result = self.binding.prepare_managed_report(
            self.action.id, self._request(device_id="another-device")
        )
        self.assertTrue(result["handled"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(Sequence.search_count([]), before)

    def test_replaced_revision_rejects_old_action(self):
        self.device_binding.active_revision_id = False
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def test_source_company_cannot_follow_selected_site(self):
        other = self.env["res.company"].create({"name": "Another report company"})
        self.partner.company_id = other
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def test_unknown_automatic_site_resolver_fails(self):
        self.binding.report_route = "automatic"
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def test_report_groups_are_checked(self):
        group = self.env["res.groups"].create({"name": "Restricted report"})
        self.report.group_ids = group
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def test_archived_site_fails_before_sequence_creation(self):
        self.site.sudo().active = False
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def test_changed_driver_profile_requires_another_device_test(self):
        self.revision.device_id.sudo().driver_profile_digest = "changed-profile"
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        self.assertEqual(result["status"], "failed")

    def _operator(self):
        return self.env["res.users"].create(
            {
                "name": "Report Operator",
                "login": "inari-report-operator",
                "company_id": self.env.company.id,
                "company_ids": [(6, 0, self.env.company.ids)],
                "group_ids": [
                    (
                        6,
                        0,
                        [
                            self.env.ref("base.group_user").id,
                            self.env.ref("inari_devices.group_inari_operator").id,
                        ],
                    )
                ],
            }
        )

    def test_ticket_is_bound_to_the_actor(self):
        result = self.binding.prepare_managed_report(self.action.id, self._request())
        Sequence = self.env["inari.report.sequence"]
        sequence = Sequence._for_action_ticket(result["action_ticket"])
        self.assertEqual(sequence.id, result["report_sequence_id"])
        with self.assertRaises(ReportRequestError):
            Sequence.with_user(self._operator())._for_action_ticket(
                result["action_ticket"]
            )

    def test_operator_cannot_rewrite_prepared_authority(self):
        operator = self._operator()
        result = self.binding.with_user(operator).prepare_managed_report(
            self.action.id, self._request()
        )
        self.assertEqual(result["status"], "prepared")
        sequence = (
            self.env["inari.report.sequence"]
            .with_user(operator)
            .browse(result["report_sequence_id"])
        )
        self.assertEqual(sequence.actor_id, operator)
        with self.assertRaises(AccessError):
            sequence.write({"actor_id": self.env.uid})

    def test_source_record_rules_are_checked(self):
        operator = self._operator()
        self.env["ir.rule"].create(
            {
                "name": "Deny the report source",
                "model_id": self.env["ir.model"]._get("res.partner").id,
                "domain_force": repr([("id", "!=", self.partner.id)]),
            }
        )
        result = self.binding.with_user(operator).prepare_managed_report(
            self.action.id, self._request()
        )
        self.assertEqual(result["status"], "failed")
