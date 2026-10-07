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

    @http.route(
        "/inari_devices/pairing/v1/device-test-assertion",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
        cors=None,
        save_session=True,
    )
    def issue_device_test_assertion(
        self, pairing_request=None, pos_session_id=None, binding_revision_id=None
    ):
        return request.env["inari.pairing.assertion"].issue_for_device_test(
            pairing_request, pos_session_id, binding_revision_id
        )
