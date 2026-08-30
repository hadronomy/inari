from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from inari.client_trust import ClientTrustError, EndpointBinding, Permission
from inari.client_trust.request import BrowserRequest, RequestTargetError
from inari.local_api.client_trust_authorizer import BrowserClientTrustAuthorizer
from inari.local_api.header_authorization import (
    AuthorizationMode,
    EndpointAuthorizationPolicy,
    HeaderAuthorizationRequest,
)


@dataclass(slots=True)
class RecordingTrust:
    accepted: object
    request: BrowserRequest | None = None
    binding: EndpointBinding | None = None
    permission: Permission | None = None

    def authorize_request(
        self,
        request: BrowserRequest,
        *,
        binding: EndpointBinding,
        permission: Permission,
    ) -> object:
        self.request = request
        self.binding = binding
        self.permission = permission
        return self.accepted


def _request(*, root_path: str = "") -> HeaderAuthorizationRequest:
    return HeaderAuthorizationRequest(
        method="POST",
        path="/v1/device-work",
        scheme="https",
        raw_path=b"/v1/device-work",
        query_string=b"",
        root_path=root_path,
        headers=(
            (b"host", b"agent.example"),
            (b"origin", b"https://odoo.example"),
            (b"authorization", b"DPoP access-token"),
            (b"dpop", b"proof"),
        ),
        server=("agent.example", 443),
        client=("127.0.0.1", 41000),
    )


def _policy() -> EndpointAuthorizationPolicy:
    return EndpointAuthorizationPolicy(
        AuthorizationMode.CLIENT_GRANT,
        permission=Permission.RECEIPT_IMAGE,
        name="receipt image submission",
    )


def test_adapter_normalizes_headers_and_passes_a_static_endpoint_binding() -> None:
    accepted = object()
    trust = RecordingTrust(accepted)
    authorizer = BrowserClientTrustAuthorizer(
        trust=cast(Any, trust),
        agent_id="agent_1",
        audience="inari.local",
    )

    decision = authorizer.authorize(_request(), _policy())

    assert decision.authorized_result is accepted
    assert trust.request is not None
    assert trust.request.target.method == "POST"
    assert trust.request.target.htu == "https://agent.example/v1/device-work"
    assert trust.request.origin == "https://odoo.example"
    assert trust.binding is not None
    assert trust.binding.agent_id == "agent_1"
    assert trust.binding.audience == "inari.local"
    assert trust.binding.agent_endpoint.value == "https://agent.example"
    assert trust.binding.allowed_methods == {"POST"}
    assert trust.binding.allowed_paths == ("/v1/device-work",)
    assert trust.permission is Permission.RECEIPT_IMAGE


def test_adapter_rejects_a_mounted_or_insecure_target() -> None:
    authorizer = BrowserClientTrustAuthorizer(
        trust=cast(Any, RecordingTrust(object())),
        agent_id="agent_1",
        audience="inari.local",
    )

    with pytest.raises(RequestTargetError):
        authorizer.authorize(_request(root_path="/mounted"), _policy())

    insecure = _request()
    insecure = HeaderAuthorizationRequest(
        method=insecure.method,
        path=insecure.path,
        scheme="http",
        raw_path=insecure.raw_path,
        query_string=insecure.query_string,
        root_path=insecure.root_path,
        headers=insecure.headers,
        server=("agent.example", 80),
        client=insecure.client,
    )
    with pytest.raises(ClientTrustError):
        authorizer.authorize(insecure, _policy())
