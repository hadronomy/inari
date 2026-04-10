{
    "name": "POS IoT Bridge",
    "summary": "Community IoT bridge for POS local hardware",
    "version": "19.0.1.0.0",
    "author": "OpenAI",
    "license": "LGPL-3",
    "depends": ["point_of_sale"],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_config_views.xml",
        "views/res_config_settings_views.xml",
        "data/ir_config_parameter_data.xml",
    ],
    "assets": {
        "point_of_sale.assets": [
            "pos_iot_bridge/static/src/js/pos_iot_bridge_service.js",
            "pos_iot_bridge/static/src/js/pos_receipt_screen_patch.js",
            "pos_iot_bridge/static/src/js/pos_order_model_patch.js",
            "pos_iot_bridge/static/src/js/pos_connection_status_patch.js",
            "pos_iot_bridge/static/src/js/debug_tools.js",
            "pos_iot_bridge/static/src/xml/pos_iot_bridge.xml",
            "pos_iot_bridge/static/src/scss/pos_iot_bridge.scss",
        ],
    },
    "installable": True,
    "application": False,
}
