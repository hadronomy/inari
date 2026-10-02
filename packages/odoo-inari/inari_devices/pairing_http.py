from odoo import http
from odoo.http import request


class InariPairingController(http.Controller):
    @http.route(
        "/inari_devices/pairing/v1/assertion",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
        cors=None,
        save_session=True,
    )
    def issue_assertion(self, pairing_request=None, pos_session_id=None):
        return request.env["inari.pairing.assertion"].issue_for_pos(
            pairing_request, pos_session_id
        )
