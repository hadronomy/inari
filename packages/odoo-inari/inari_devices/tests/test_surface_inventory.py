from pathlib import Path
import re

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestInariSurfaceInventory(TransactionCase):
    def test_installed_surfaces_have_models_or_methods(self):
        module = Path(__file__).parents[1]
        manifest = (module / "__manifest__.py").read_text()
        self.assertIn("data/inari_devices_data.xml", manifest)
        self.assertIn("views/inari_devices_views.xml", manifest)
        for xml_path in (module / "data").glob("*.xml"):
            xml = xml_path.read_text()
            for code in re.findall(r'<field name="code">([^<]+)</field>', xml):
                self.assertRegex(code, r"model\.[A-Za-z_]+\(\)")
        actions = self.env["ir.actions.act_window"].search([("res_model", "=", "inari.device")])
        self.assertTrue(actions)
