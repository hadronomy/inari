from __future__ import annotations

import json
from dataclasses import dataclass, field
from collections.abc import MutableMapping
from typing import Any

import pytest

from inari.client_trust import Permission
from inari.local_api.header_authorization import (
    AUTHORIZATION_RESULT_STATE_KEY,
    PAIRING_PERMIT_STATE_KEY,
    AuthorizationDecision,
    AuthorizationFailure,
    AuthorizationProblem,
    AuthorizationMode,
    EndpointAuthorizationPolicy,
    ExplicitEndpointPolicyCatalog,
    HeaderAuthorizationMiddleware,
    HeaderAuthorizationRequest,
)


def _scope(
    *,
    method: str = "POST",
    path: str = "/v1/device-work",
    state: dict[str, object] | None = None,
    scope_type: str = "http",
) -> dict[str, Any]:
    return {
        "type": scope_type,
        "method": method,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "scheme": "https",
        "headers": [(b"origin", b"https://odoo.example")],
        "server": ("agent.example", 443),
        "client": ("127.0.0.1", 40000),
        "state": {} if state is None else state,
    }


@dataclass(slots=True)
class RecordingAuthorizer:
    decision: AuthorizationDecision | None = None
    error: Exception | None = None
    requests: list[HeaderAuthorizationRequest] = field(default_factory=list)
    policies: list[EndpointAuthorizationPolicy] = field(default_factory=list)

    def authorize(
        self,
        request: HeaderAuthorizationRequest,
        policy: EndpointAuthorizationPolicy,
    ) -> AuthorizationDecision:
        self.requests.append(request)
        self.policies.append(policy)
        if self.error is not None:
            raise self.error
        assert self.decision is not None
        return self.decision


@dataclass(slots=True)
class RecordingMapper:
    problems: list[tuple[Exception, HeaderAuthorizationRequest]] = field(
        default_factory=list
    )
    problem: AuthorizationProblem = AuthorizationProblem(
        type_uri="urn:inari:problem:v1:authorization_required",
        title="Authorization required",
        status=401,
        detail="The request is not authorized.",
        extensions={"error_code": "AUTHORIZATION_REQUIRED"},
        headers={"WWW-Authenticate": 'DPoP realm="inari"'},
    )

    def map_error(
        self,
        error: Exception,
        request: HeaderAuthorizationRequest,
    ) -> AuthorizationProblem:
        self.problems.append((error, request))
        return self.problem


@dataclass(slots=True)
class RecordingApplication:
    calls: list[tuple[object, object]] = field(default_factory=list)
    received: list[MutableMapping[str, Any]] = field(default_factory=list)

    async def __call__(self, scope, receive, send) -> None:
        self.calls.append((scope, receive))
        self.received.append(await receive())
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})


@pytest.mark.anyio
async def test_authorization_failure_is_problem_json_without_reading_body() -> None:
    app = RecordingApplication()
    authorizer = RecordingAuthorizer(
        error=AuthorizationFailure(
            "missing_client_grant", "The request needs a Client Grant."
        )
    )
    mapper = RecordingMapper()
    middleware = HeaderAuthorizationMiddleware(
        app,
        catalog=ExplicitEndpointPolicyCatalog(
            {
                ("POST", "/v1/device-work"): EndpointAuthorizationPolicy(
                    AuthorizationMode.CLIENT_GRANT,
                    permission=Permission.RECEIPT_IMAGE,
                )
            }
        ),
        authorizer=authorizer,
        error_mapper=mapper,
    )
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"secret", "more_body": False}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = _scope()
    await middleware(scope, receive, send)

    assert receive_calls == 0
    assert app.calls == []
    assert len(mapper.problems) == 1
    assert mapper.problems[0][0] is authorizer.error
    assert mapper.problems[0][1].path == "/v1/device-work"
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    headers = dict(sent[0]["headers"])
    assert headers[b"content-type"] == b"application/problem+json"
    assert headers[b"www-authenticate"] == b'DPoP realm="inari"'
    assert json.loads(sent[1]["body"]) == {
        "type": "urn:inari:problem:v1:authorization_required",
        "title": "Authorization required",
        "status": 401,
        "detail": "The request is not authorized.",
        "error_code": "AUTHORIZATION_REQUIRED",
    }


@pytest.mark.anyio
async def test_success_passes_the_same_scope_and_receive_and_stores_grant() -> None:
    app = RecordingApplication()
    authorizer = RecordingAuthorizer(decision=AuthorizationDecision.authorized("grant"))
    mapper = RecordingMapper()
    middleware = HeaderAuthorizationMiddleware(
        app,
        catalog=ExplicitEndpointPolicyCatalog(
            {
                ("post", "/v1/device-work"): EndpointAuthorizationPolicy(
                    AuthorizationMode.CLIENT_GRANT,
                    permission=Permission.RECEIPT_IMAGE,
                )
            }
        ),
        authorizer=authorizer,
        error_mapper=mapper,
    )
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"work", "more_body": False}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    scope = _scope(state={"existing": "value"})
    await middleware(scope, receive, send)

    assert app.calls == [(scope, receive)]
    assert app.received == [
        {"type": "http.request", "body": b"work", "more_body": False}
    ]
    assert receive_calls == 1
    assert scope["state"] == {
        "existing": "value",
        AUTHORIZATION_RESULT_STATE_KEY: "grant",
    }
    assert mapper.problems == []
    assert sent[0]["status"] == 204


@pytest.mark.anyio
async def test_pairing_policy_stores_only_pairing_permit_and_receives_exact_policy() -> (
    None
):
    app = RecordingApplication()
    policy = EndpointAuthorizationPolicy(
        AuthorizationMode.PAIRING,
        name="pairing assertion",
    )
    authorizer = RecordingAuthorizer(decision=AuthorizationDecision.paired("permit"))
    middleware = HeaderAuthorizationMiddleware(
        app,
        catalog=ExplicitEndpointPolicyCatalog({("POST", "/pairing/v1/start"): policy}),
        authorizer=authorizer,
        error_mapper=RecordingMapper(),
    )
    scope = _scope(path="/pairing/v1/start")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    await middleware(scope, receive, _noop_send)

    assert authorizer.policies == [policy]
    assert scope["state"] == {PAIRING_PERMIT_STATE_KEY: "permit"}


async def _noop_send(message: MutableMapping[str, Any]) -> None:
    del message


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scope_type", "method", "path"),
    [
        ("websocket", "GET", "/v1/device-work"),
        ("http", "GET", "/health"),
        ("http", "POST", "/unlisted"),
    ],
)
async def test_unprotected_requests_bypass_authorization_and_body_read(
    scope_type: str, method: str, path: str
) -> None:
    app = RecordingApplication()
    authorizer = RecordingAuthorizer(
        error=AuthorizationFailure("must_not_run", "must not run")
    )
    middleware = HeaderAuthorizationMiddleware(
        app,
        catalog=ExplicitEndpointPolicyCatalog(
            {("GET", "/health"): EndpointAuthorizationPolicy(AuthorizationMode.PUBLIC)}
        ),
        authorizer=authorizer,
        error_mapper=RecordingMapper(),
    )
    scope = _scope(scope_type=scope_type, method=method, path=path)
    receive_calls = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.disconnect"}

    sent: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    assert authorizer.requests == []
    assert app.calls == [(scope, receive)]
    assert receive_calls == 1
    assert sent[0]["status"] == 204


def test_catalog_normalizes_method_and_rejects_duplicate_entries() -> None:
    policy = EndpointAuthorizationPolicy(AuthorizationMode.PUBLIC)
    catalog = ExplicitEndpointPolicyCatalog({(" post ", " /health"): policy})

    assert catalog.policy_for("POST", "/health") is policy
    with pytest.raises(ValueError, match="duplicate endpoint policy"):
        ExplicitEndpointPolicyCatalog(
            {
                ("GET", "/health"): policy,
                (" get ", "/health"): policy,
            }
        )


def test_problem_extensions_cannot_override_rfc9457_members() -> None:
    problem = AuthorizationProblem(
        type_uri="urn:test",
        title="Bad",
        status=400,
        detail="Bad request",
        extensions={"status": 200},
    )

    with pytest.raises(ValueError, match="RFC 9457"):
        problem.document()
