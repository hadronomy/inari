from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, tagged

from ..services.inventory import (
    Inventory,
    InventoryAgent,
    InventoryDevice,
    InventorySite,
)


@tagged("post_install", "-at_install")
class TestInariInventory(TransactionCase):
    def setUp(self):
        super().setUp()
        self.setup = self.env["inari.setup.state"].create(
            {"company_id": self.env.company.id}
        )
        self.snapshot = Inventory(
            "MIZONA",
            datetime(2026, 10, 7, 12),
            (InventorySite("site_store", "Store"),),
            (
                InventoryAgent(
                    "agt_workstation", "site_store", "online", datetime(2026, 10, 7, 12)
                ),
            ),
            (
                InventoryDevice(
                    "dev_printer",
                    "agt_workstation",
                    "site_store",
                    "POS-80",
                    "printer",
                    "physical",
                    "online",
                    "spooler",
                    datetime(2026, 10, 7, 12),
                ),
            ),
        )

    def test_sync_is_company_scoped_and_does_not_claim_business_readiness(self):
        self.setup._apply_inventory(self.snapshot, "org_mizona")
        device = self.env["inari.device"].search(
            [("company_id", "=", self.env.company.id)]
        )
        self.assertEqual(device.name, "POS-80")
        self.assertEqual(device.connection_state, "online")
        self.assertEqual(device.health_state, "unknown")
        self.assertFalse(device.capability_ids)
        self.assertFalse(device.driver_profile_digest)
        other_company = self.env["res.company"].create({"name": "Other company"})
        other_setup = self.env["inari.setup.state"].create(
            {"company_id": other_company.id}
        )
        other_setup._apply_inventory(self.snapshot, "org_mizona")
        other_device = (
            self.env["inari.device"]
            .sudo()
            .search([("company_id", "=", other_company.id)])
        )
        self.assertNotEqual(other_device.id, device.id)
        self.setup._apply_inventory(
            replace(
                self.snapshot,
                observed_at=self.snapshot.observed_at + timedelta(seconds=1),
                sites=(),
                agents=(),
                devices=(),
            ),
            "org_mizona",
        )
        self.assertFalse(device.active)
        self.assertTrue(other_device.active)

    def test_device_identity_is_scoped_to_its_agent(self):
        second_agent = replace(self.snapshot.agents[0], agent_id="agt_second")
        second_device = replace(self.snapshot.devices[0], agent_id="agt_second")
        self.setup._apply_inventory(
            replace(
                self.snapshot,
                agents=(*self.snapshot.agents, second_agent),
                devices=(*self.snapshot.devices, second_device),
            ),
            "org_mizona",
        )
        devices = self.env["inari.device"].search(
            [("company_id", "=", self.env.company.id)]
        )
        self.assertEqual(len(devices), 2)
        self.assertEqual(
            set(devices.mapped("controller_uuid")),
            {"agt_workstation/dev_printer", "agt_second/dev_printer"},
        )

    def test_stale_inventory_cannot_archive_current_devices(self):
        self.setup._apply_inventory(self.snapshot, "org_mizona")
        with self.assertRaises(ValidationError):
            self.setup._apply_inventory(
                replace(
                    self.snapshot,
                    observed_at=self.snapshot.observed_at - timedelta(seconds=1),
                    devices=(),
                ),
                "org_mizona",
            )
        self.assertEqual(self.env["inari.device"].search_count([]), 1)

    def test_decommissioned_connection_and_organization_change_are_rejected(self):
        self.setup._apply_inventory(self.snapshot, "org_mizona")
        with self.assertRaises(ValidationError):
            self.setup._apply_inventory(self.snapshot, "org_other")
        self.setup.state = "decommissioned"
        with self.assertRaises(UserError):
            self.setup._apply_inventory(self.snapshot, "org_mizona")

    def test_operator_cannot_connect_or_sync(self):
        user = self.env["res.users"].create(
            {
                "name": "Inventory operator",
                "login": "inari-inventory-operator",
                "group_ids": [
                    (6, 0, [self.env.ref("inari_devices.group_inari_operator").id])
                ],
            }
        )
        with self.assertRaises(AccessError):
            self.setup.with_user(user).action_connect()
        with self.assertRaises(AccessError):
            self.setup.with_user(user).action_sync_inventory()

    def test_invalid_inventory_rolls_back_the_whole_company_snapshot(self):
        self.setup._apply_inventory(self.snapshot, "org_mizona")
        invalid_device = replace(self.snapshot.devices[0], site_id="site_absent")
        with self.assertRaises(KeyError), self.env.cr.savepoint():
            self.setup._apply_inventory(
                replace(
                    self.snapshot,
                    organization_name="Changed",
                    devices=(invalid_device,),
                ),
                "org_mizona",
            )
        self.assertEqual(self.env["inari.organization"].search([]).name, "MIZONA")

    def test_cron_records_failure_without_archiving_devices(self):
        self.setup._apply_inventory(self.snapshot, "org_mizona")
        self.setup.controller_url = "https://controller.example.com"
        with patch.object(
            type(self.setup), "_sync_inventory", side_effect=UserError("Unavailable")
        ):
            self.setup._cron_sync_inventory()
        self.assertEqual(self.setup.state, "blocked")
        self.assertEqual(self.env["inari.device"].search_count([]), 1)
