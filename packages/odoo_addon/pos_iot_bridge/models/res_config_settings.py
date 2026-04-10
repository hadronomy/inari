from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pos_iot_bridge_default_agent_url = fields.Char(
        string="Default POS IoT Bridge URL",
        config_parameter="pos_iot_bridge.default_agent_url",
        default="http://127.0.0.1:7310",
    )
