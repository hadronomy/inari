from ast import literal_eval
from pathlib import Path


ADDON = Path(__file__).parents[1] / "inari_devices"


def manifest():
    return literal_eval((ADDON / "__manifest__.py").read_text(encoding="utf-8"))


def test_manifest_targets_odoo_19_pos_assets_only():
    values = manifest()

    assert values["version"].startswith("19.0.")
    assert "point_of_sale" in values["depends"]
    assert "web.assets_backend" not in values["assets"]
    assert values["assets"]["point_of_sale._assets_pos"][-1] == (
        "inari_devices/static/src/index.js"
    )
    assert values["assets"]["web.assets_unit_tests"] == [
        "inari_devices/static/tests/unit/**/*.js"
    ]


def test_manifest_data_and_assets_exist():
    values = manifest()
    paths = [*values["data"]]
    for entries in values["assets"].values():
        paths.extend(path for path in entries if "*" not in path)

    for relative in paths:
        path = relative.removeprefix("inari_devices/")
        assert (ADDON / path).is_file(), relative


def test_pos_patch_keeps_native_printer_ownership_outside_inari():
    source = (ADDON / "static/src/pos_printer_patch.js").read_text(encoding="utf-8")

    assert "PosPrinterService.prototype" in source
    assert "new PosPrinterService(env, services)" in source
    assert "PosStore.prototype" in source
    assert "hardware_proxy.printer =" not in source
    assert "return undefined;" in source
