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


def test_input_patches_use_odoo_19_scale_and_barcode_seams():
    scale = (ADDON / "static/src/scale_patch.js").read_text(encoding="utf-8")
    scanner = (ADDON / "static/src/scanner_patch.js").read_text(encoding="utf-8")

    assert "PosScaleService.prototype" in scale
    assert 'posScaleService.dependencies.push("inari_device")' in scale
    assert "Number(reading.decimal)" in scale
    assert "BarcodeReader.prototype" in scanner
    assert "super.connectToProxy(...arguments)" in scanner
    assert "this.hardwareProxy.message" not in scanner


def test_pos_config_projects_certified_scale_and_scanner_bindings():
    config = (ADDON / "models/pos_config.py").read_text(encoding="utf-8")
    projection = (ADDON / "services/pos_binding_projections.py").read_text(
        encoding="utf-8"
    )

    assert "inari_scale_binding = fields.Json" in config
    assert "inari_scanner_binding = fields.Json" in config
    assert '"certification_required"' in projection
    assert '"certification_id": certification_id or False' in projection
