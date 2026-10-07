from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from inari.client_trust.request import RequestTargetError
from inari.local_api.endpoint import AgentEndpointMiddleware
from inari.local_api.pairing_routes import _browser_request


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scheme,port,headers,expected",
    [
        ("https", 7310, {}, 200),
        ("http", 7310, {}, 400),
        ("https", 443, {}, 400),
        ("https", 7310, {"Host": "other.example:7310"}, 400),
        ("https", 7310, {"Forwarded": "host=agent.example:7310"}, 400),
        ("https", 7310, {"X-Forwarded-Host": "agent.example:7310"}, 400),
    ],
)
async def test_configured_endpoint_keeps_browser_target_checks(
    scheme,
    port,
    headers,
    expected,
) -> None:
    async def browser(scope, receive, send):
        try:
            target = _browser_request(Request(scope)).target
            response = JSONResponse({"htu": target.htu})
        except RequestTargetError:
            response = JSONResponse({}, status_code=400)
        await response(scope, receive, send)

    boundary = AgentEndpointMiddleware(browser, endpoint="https://agent.example:7310/")

    async def socket_listener(scope, receive, send):
        await boundary(
            {**scope, "scheme": scheme, "server": ("127.0.0.1", port)},
            receive,
            send,
        )

    async with AsyncClient(
        transport=ASGITransport(app=socket_listener),
        base_url="https://agent.example:7310",
    ) as client:
        response = await client.post(
            "/pairing/v1/requests",
            headers={"Origin": "https://odoo.example", "DPoP": "proof", **headers},
        )
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {
            "htu": "https://agent.example:7310/pairing/v1/requests"
        }
