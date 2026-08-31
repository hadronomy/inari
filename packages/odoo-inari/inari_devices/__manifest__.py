{
    "name": "Inari Devices",
    "summary": "Scoped Inari device projections, bindings, and recovery",
    "description": (
        "Inari Devices\n"
        "=============\n\n"
        "Connects Odoo workflows to Inari Agents through explicit bindings and "
        "content-free audit records. Controller-owned records remain projections."
    ),
    "version": "19.0.1.0.0",
    "category": "Operations",
    "author": "Inari",
    "license": "LGPL-3",
    "depends": ["base", "point_of_sale"],
    "data": [
        "security/inari_devices_security.xml",
        "security/ir.model.access.csv",
        "data/inari_devices_data.xml",
        "views/inari_devices_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "inari_devices/static/src/submission_context.js",
            "inari_devices/static/src/recovery_store.js",
            "inari_devices/static/src/print_recovery.js",
            "inari_devices/static/src/recovery_dialog.js",
            "inari_devices/static/src/recovery_dialog.xml",
            "inari_devices/static/src/recovery_dialog.scss",
            "inari_devices/static/src/preparation_print.js",
            "inari_devices/static/src/client_pairing.js",
            "inari_devices/static/src/client_pairing_dialog.js",
            "inari_devices/static/src/client_pairing_dialog.xml",
            "inari_devices/static/src/client_pairing.scss",
            "inari_devices/static/src/agent_client.js",
            "inari_devices/static/src/inari_printer.js",
            "inari_devices/static/src/inari_device_service.js",
            "inari_devices/static/src/pos_printer_patch.js",
            "inari_devices/static/src/index.js",
        ],
        "web.assets_unit_tests": [
            "inari_devices/static/tests/unit/**/*.js",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
