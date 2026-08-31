from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request, Response

from ..client_trust import (
    BoundOrigin,
    BusinessScope,
    ClientTrustService,
    PairingCommand,
    PairingScope,
    RenewalCommand,
    RequestTarget,
    jwk_thumbprint,
)
from ..client_trust.request import (
    KeyBoundBrowserRequest,
    RequestTargetError,
    build_key_bound_browser_request,
)
from ..config import AgentSettings
from ..security.auth import AuthorizationService
from ..security.identity import AgentIdentityService
from ..security.models import AccessScope
from .dependencies import (
    get_authorization_service,
    get_client_trust_service,
    get_identity_service,
    get_settings,
)
from .problem_handlers import problem_responses
from .schemas import (
    ClientGrantRenewalInput,
    ClientGrantRenewalResponse,
    PairingAdmissionInput,
    PairingAdmissionResponse,
    PairingDecisionInput,
    PairingRequestCreateInput,
    PairingRequestResponse,
)


pairing_router = APIRouter(prefix="/pairing/v1", tags=["pairing"])

ClientTrustDependency = Annotated[ClientTrustService, Depends(get_client_trust_service)]
SettingsDependency = Annotated[AgentSettings, Depends(get_settings)]
IdentityDependency = Annotated[AgentIdentityService, Depends(get_identity_service)]
AuthorizationDependency = Annotated[
    AuthorizationService, Depends(get_authorization_service)
]

_PAIRING_PROBLEM_RESPONSES = problem_responses(400, 401, 403, 404, 409, 410, 422, 500)
_PAIRING_PROBLEM_RESPONSES[401]["headers"] = {
    "DPoP-Nonce": {
        "description": "Fresh single-use nonce for one DPoP retry.",
        "schema": {"type": "string"},
    },
    "WWW-Authenticate": {
        "description": "DPoP challenge with the `use_dpop_nonce` error.",
        "schema": {"type": "string"},
    },
    "Cache-Control": {
        "description": "Prevents storage of the nonce challenge.",
        "schema": {"type": "string", "enum": ["no-store"]},
    },
}


def _browser_request(connection: Request) -> KeyBoundBrowserRequest:
    scope = connection.scope
    server = scope.get("server")
    if server is None or scope.get("root_path"):
        raise RequestTargetError("The trusted pairing request target is incomplete.")
    return build_key_bound_browser_request(
        method=connection.method,
        scheme=connection.url.scheme,
        server=cast(tuple[str, int], server),
        path=connection.url.path,
        raw_path=cast(bytes | None, scope.get("raw_path")),
        query_string=cast(bytes, scope.get("query_string", b"")),
        headers=cast(tuple[tuple[bytes, bytes], ...], scope.get("headers", ())),
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@pairing_router.post(
    "/requests",
    response_model=PairingRequestResponse,
    status_code=201,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def create_client_pairing_request(
    payload: PairingRequestCreateInput,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
    settings: SettingsDependency,
    identity_service: IdentityDependency,
) -> PairingRequestResponse:
    browser = _browser_request(connection)
    public_jwk = payload.browser_jwk.model_dump()
    thumbprint = jwk_thumbprint(public_jwk)
    trust.authorize_pairing_request(browser, jwk_thumbprint=thumbprint)
    identity = identity_service.get_or_create_identity()
    request = trust.create_pairing_request(
        scope=PairingScope(
            agent_id=identity.agent_id,
            browser_origin=BoundOrigin(browser.origin),
            agent_endpoint=BoundOrigin(
                f"{browser.target.scheme}://{browser.target.authority}"
            ),
            business=BusinessScope(**payload.business.model_dump()),
            audience=settings.token_audience,
        ),
        browser_jwk_thumbprint=thumbprint,
        requested_permissions=payload.requested_permissions,
    )
    _no_store(response)
    return PairingRequestResponse.from_domain(request)


@pairing_router.get(
    "/requests/{request_id}",
    response_model=PairingRequestResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def get_client_pairing_request(
    request_id: str,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
) -> PairingRequestResponse:
    request = trust.require_pairing_request(request_id)
    trust.authorize_pairing_request(
        _browser_request(connection),
        jwk_thumbprint=request.browser_jwk_thumbprint,
        expected_origin=request.scope.browser_origin,
    )
    _no_store(response)
    return PairingRequestResponse.from_domain(request)


@pairing_router.post(
    "/requests/{request_id}/cancel",
    response_model=PairingRequestResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def cancel_client_pairing_request(
    request_id: str,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
) -> PairingRequestResponse:
    request = trust.require_pairing_request(request_id)
    trust.authorize_pairing_request(
        _browser_request(connection),
        jwk_thumbprint=request.browser_jwk_thumbprint,
        expected_origin=request.scope.browser_origin,
    )
    canceled = trust.cancel_pairing_request(request_id)
    _no_store(response)
    return PairingRequestResponse.from_domain(canceled)


@pairing_router.post(
    "/requests/{request_id}/admit",
    response_model=PairingAdmissionResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def admit_client_pairing(
    request_id: str,
    payload: PairingAdmissionInput,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
) -> PairingAdmissionResponse:
    request = trust.require_pairing_request(request_id)
    trust.authorize_pairing_request(
        _browser_request(connection),
        jwk_thumbprint=request.browser_jwk_thumbprint,
        expected_origin=request.scope.browser_origin,
    )
    result = trust.admit_pairing(
        PairingCommand(request_id=request_id, assertion=payload.assertion)
    )
    _no_store(response)
    return PairingAdmissionResponse.from_domain(result)


@pairing_router.post(
    "/client-grants/renew",
    response_model=ClientGrantRenewalResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def renew_client_grant(
    payload: ClientGrantRenewalInput,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
) -> ClientGrantRenewalResponse:
    browser = _browser_request(connection)
    result = trust.renew_grant(
        RenewalCommand(
            pairing_id=payload.pairing_id,
            grant_id=payload.grant_id,
            target=RequestTarget(browser.target.method, browser.target.htu),
            browser_origin=BoundOrigin(browser.origin),
            dpop=browser.dpop,
        )
    )
    _no_store(response)
    return ClientGrantRenewalResponse.from_domain(result)


@pairing_router.get(
    "/requests/{request_id}/review",
    response_model=PairingRequestResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def review_client_pairing_request(
    request_id: str,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
    authorization: AuthorizationDependency,
) -> PairingRequestResponse:
    principal = authorization.authenticate_connection(connection)
    authorization.require_scopes(principal, (AccessScope.ADMIN_READ,))
    request = trust.require_pairing_request(request_id)
    _no_store(response)
    return PairingRequestResponse.from_domain(request)


@pairing_router.post(
    "/requests/{request_id}/decision",
    response_model=PairingRequestResponse,
    responses=_PAIRING_PROBLEM_RESPONSES,
)
async def decide_client_pairing_request(
    request_id: str,
    payload: PairingDecisionInput,
    connection: Request,
    response: Response,
    trust: ClientTrustDependency,
    authorization: AuthorizationDependency,
) -> PairingRequestResponse:
    principal = authorization.authenticate_connection(connection)
    authorization.require_scopes(principal, (AccessScope.ADMIN_WRITE,))
    request = (
        trust.approve_pairing_request(request_id)
        if payload.decision == "approve"
        else trust.deny_pairing_request(request_id)
    )
    _no_store(response)
    return PairingRequestResponse.from_domain(request)


__all__ = ["pairing_router"]
