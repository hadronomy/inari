from ast import literal_eval
import json
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless

from odoo.tests.common import TransactionCase, tagged
from odoo.tools.js_transpiler import transpile_javascript


@tagged("post_install", "-at_install")
class TestInariPosAssets(TransactionCase):
    @skipUnless(shutil.which("node"), "Node is required to parse browser scripts")
    def test_pos_modules_compile_to_browser_scripts(self):
        addon = Path(__file__).parents[1]
        manifest = literal_eval((addon / "__manifest__.py").read_text())
        scripts = {
            path: transpile_javascript("/" + path, (addon.parent / path).read_text())
            for path in manifest["assets"]["point_of_sale._assets_pos"]
            if path.endswith(".js")
        }
        result = subprocess.run(
            [
                "node",
                "--input-type=commonjs",
                "-e",
                "const vm = require('node:vm');"
                "const fs = require('node:fs');"
                "const scripts = JSON.parse(fs.readFileSync(0, 'utf8'));"
                "for (const [filename, code] of Object.entries(scripts)) {"
                " new vm.Script(code, { filename });"
                "}",
            ],
            input=json.dumps(scripts),
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
