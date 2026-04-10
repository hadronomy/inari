from odoo.tests.common import TransactionCase


class TestPosIotBridgeConfig(TransactionCase):
    def test_public_config(self):
        config = self.env["pos.config"].create({
            "name": "Test POS",
            "iot_bridge_enabled": True,
            "iot_bridge_agent_url": "http://127.0.0.1:7310",
            "iot_bridge_receipt_mode": "payload",
        })
        payload = config.get_iot_bridge_public_config()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["agent_url"], "http://127.0.0.1:7310")
        self.assertEqual(payload["receipt_mode"], "payload")
