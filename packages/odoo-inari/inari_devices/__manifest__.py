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
    "installable": True,
    "application": True,
    "auto_install": False,
}
