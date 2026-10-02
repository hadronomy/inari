from __future__ import annotations

from dataclasses import dataclass

from ..client_trust import BoundOrigin, ClientTrustService, EndpointBinding
from ..client_trust.request import RequestTargetError, build_browser_request
from .header_authorization import (
    AuthorizationDecision,
    AuthorizationFailure,
    AuthorizationMode,
    EndpointAuthorizationPolicy,
    HeaderAuthorizationRequest,
)


@dataclass(frozen=True, slots=True)
class BrowserClientTrustAuthorizer:
    """Authorize one protected browser request without reading its body."""

    trust: ClientTrustService
    agent_id: str
    audience: str

    def authorize(
        self,
        request: HeaderAuthorizationRequest,
        policy: EndpointAuthorizationPolicy,
    ) -> AuthorizationDecision:
        if policy.mode is not AuthorizationMode.CLIENT_GRANT:
            raise AuthorizationFailure(
                "invalid_authorization_policy",
                "The header adapter requires a Client Grant policy.",
                status=500,
            )
        if policy.permission is None:
            raise AuthorizationFailure(
                "invalid_authorization_policy",
                "The header adapter requires one Client Grant permission.",
                status=500,
            )
        if request.server is None or request.root_path:
            raise RequestTargetError("The trusted request target is incomplete.")

        browser_request = build_browser_request(
            method=request.method,
            scheme=request.scheme,
            server=request.server,
            path=request.path,
            raw_path=request.raw_path,
            query_string=request.query_string,
            headers=request.headers,
        )
        binding = EndpointBinding(
            agent_id=self.agent_id,
            audience=self.audience,
            agent_endpoint=BoundOrigin(
                f"{browser_request.target.scheme}://{browser_request.target.authority}"
            ),
            allowed_methods=frozenset({browser_request.target.method}),
            allowed_paths=(browser_request.target.path,),
        )
        authorized = self.trust.authorize_request(
            browser_request,
            binding=binding,
            permission=policy.permission,
        )
        return AuthorizationDecision.authorized(authorized)


__all__ = ["BrowserClientTrustAuthorizer"]
