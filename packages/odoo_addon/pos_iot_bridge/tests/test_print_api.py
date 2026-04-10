from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestPosIotBridgeApi(HttpCase):
    def test_health_endpoint(self):
        self.authenticate("admin", "admin")
        response = self.url_open(
            "/web/dataset/call_kw",
            data='{"jsonrpc":"2.0","method":"call","params":{"model":"pos.config","method":"search_read","args":[[]],"kwargs":{"limit":1}},"id":1}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200)
