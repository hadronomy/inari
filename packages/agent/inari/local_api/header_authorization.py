from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, cast

from ..client_trust import Permission
from ..client_trust.errors import ClientTrustError, ClientTrustErrorCode
from ..core.failures import DomainFailure, ProblemCode
from ..core.problems import problem_from_failure


ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApplication = Callable[
    [MutableMapping[str, Any], ASGIReceive, ASGISend], Awaitable[None]
]

AUTHORIZATION_RESULT_STATE_KEY = "inari.authorization_result"
PAIRING_PERMIT_STATE_KEY = "inari.pairing_permit"
PROBLEM_MEDIA_TYPE = "application/problem+json"


class AuthorizationMode(StrEnum):
    PUBLIC = "public"
    CLIENT_GRANT = "client_grant"
    PAIRING = "pairing"


@dataclass(frozen=True, slots=True)
class EndpointAuthorizationPolicy:
    mode: AuthorizationMode
    permission: Permission | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if self.mode is AuthorizationMode.CLIENT_GRANT:
            if not isinstance(self.permission, Permission):
                raise ValueError("Client Grant policies require one permission")
        elif self.permission is not None:
            raise ValueError("only Client Grant policies can require a permission")


class EndpointPolicyCatalog(Protocol):
    def policy_for(
        self, method: str, path: str
    ) -> EndpointAuthorizationPolicy | None: ...


@dataclass(frozen=True, slots=True)
class ExplicitEndpointPolicyCatalog:
    policies: Mapping[tuple[str, str], EndpointAuthorizationPolicy]

    def __post_init__(self) -> None:
        normalized: dict[tuple[str, str], EndpointAuthorizationPolicy] = {}
        for (method, path), policy in self.policies.items():
            normalized_method = method.strip().upper()
            normalized_path = path.strip()
            if not normalized_method or not normalized_path.startswith("/"):
                raise ValueError("endpoint policies require an HTTP method and path")
            key = (normalized_method, normalized_path)
            if key in normalized:
                raise ValueError(f"duplicate endpoint policy: {method} {path}")
            normalized[key] = policy
        object.__setattr__(self, "policies", MappingProxyType(normalized))

    def policy_for(self, method: str, path: str) -> EndpointAuthorizationPolicy | None:
        return self.policies.get((method.upper(), path))


@dataclass(frozen=True, slots=True)
class HeaderAuthorizationRequest:
    method: str
    path: str
    scheme: str
    raw_path: bytes
    query_string: bytes
    root_path: str
    headers: tuple[tuple[bytes, bytes], ...]
    server: tuple[str, int] | None
    client: tuple[str, int] | None

    @classmethod
    def from_scope(cls, scope: Mapping[str, Any]) -> HeaderAuthorizationRequest:
        raw_headers = scope.get("headers", ())
        headers = tuple((bytes(name), bytes(value)) for name, value in raw_headers)
        raw_path = scope.get("raw_path", b"")
        query_string = scope.get("query_string", b"")
        return cls(
            method=str(scope.get("method", "")),
            path=str(scope.get("path", "")),
            scheme=str(scope.get("scheme", "")),
            raw_path=bytes(raw_path),
            query_string=bytes(query_string),
            root_path=str(scope.get("root_path", "")),
            headers=headers,
            server=_address(scope.get("server")),
            client=_address(scope.get("client")),
        )


def _address(value: object) -> tuple[str, int] | None:
    if not isinstance(value, (tuple, list)) or len(value) < 2:
        return None
    host, port = value[0], value[1]
    if not isinstance(host, str) or isinstance(port, bool) or not isinstance(port, int):
        return None
    return host, port


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    authorized_result: object | None = None
    pairing_permit: object | None = None

    def __post_init__(self) -> None:
        if (self.authorized_result is None) == (self.pairing_permit is None):
            raise ValueError(
                "an authorization decision must contain exactly one result"
            )

    @classmethod
    def authorized(cls, result: object) -> AuthorizationDecision:
        return cls(authorized_result=result)

    @classmethod
    def paired(cls, permit: object) -> AuthorizationDecision:
        return cls(pairing_permit=permit)


class ClientTrustAuthorizer(Protocol):
    def authorize(
        self,
        request: HeaderAuthorizationRequest,
        policy: EndpointAuthorizationPolicy,
    ) -> AuthorizationDecision | Awaitable[AuthorizationDecision]: ...


class AuthorizationFailure(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        *,
        status: int = 401,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = status
        self.headers = MappingProxyType(dict(headers or {}))


@dataclass(frozen=True, slots=True)
class AuthorizationProblem:
    type_uri: str
    title: str
    status: int
    detail: str
    instance: str | None = None
    extensions: Mapping[str, object] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)

    def document(self) -> dict[str, object]:
        reserved = {"type", "title", "status", "detail", "instance"}
        if reserved.intersection(self.extensions):
            raise ValueError("problem extensions cannot override RFC 9457 members")
        document: dict[str, object] = {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            **dict(self.extensions),
        }
        if self.instance is not None:
            document["instance"] = self.instance
        return document


class ProblemAuthorizationErrorMapper:
    """Map header-only authorization failures to the public problem catalog."""

    def map_error(
        self,
        error: Exception,
        request: HeaderAuthorizationRequest,
    ) -> AuthorizationProblem:
        code = _authorization_problem_code(error)
        correlation_id = _correlation_id(request)
        problem = problem_from_failure(
            DomainFailure(code), correlation_id=correlation_id
        )
        document = problem.to_dict()
        extensions = {
            name: value
            for name, value in document.items()
            if name not in {"type", "title", "status", "detail", "instance"}
        }
        headers = {"X-Correlation-ID": problem.correlation_id}
        if problem.status == 401:
            headers["WWW-Authenticate"] = 'DPoP realm="inari"'
        return AuthorizationProblem(
            type_uri=problem.type,
            title=problem.title,
            status=problem.status,
            detail=problem.detail,
            instance=problem.instance,
            extensions=extensions,
            headers=headers,
        )


class AuthorizationErrorMapper(Protocol):
    def map_error(
        self,
        error: Exception,
        request: HeaderAuthorizationRequest,
    ) -> AuthorizationProblem: ...


class HeaderAuthorizationMiddleware:
    def __init__(
        self,
        app: ASGIApplication,
        *,
        catalog: EndpointPolicyCatalog,
        authorizer: ClientTrustAuthorizer,
        error_mapper: AuthorizationErrorMapper,
    ) -> None:
        self._app = app
        self._catalog = catalog
        self._authorizer = authorizer
        self._error_mapper = error_mapper

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        policy = self._catalog.policy_for(
            str(scope.get("method", "")), str(scope.get("path", ""))
        )
        if policy is None or policy.mode is AuthorizationMode.PUBLIC:
            await self._app(scope, receive, send)
            return

        request = HeaderAuthorizationRequest.from_scope(scope)
        try:
            decision = self._authorizer.authorize(request, policy)
            if inspect.isawaitable(decision):
                decision = await decision
            self._store_decision(scope, policy, decision)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        except Exception as error:
            problem = self._error_mapper.map_error(error, request)
            await _send_problem(send, problem)
            return

        await self._app(scope, receive, send)

    @staticmethod
    def _store_decision(
        scope: MutableMapping[str, Any],
        policy: EndpointAuthorizationPolicy,
        decision: object,
    ) -> None:
        if not isinstance(decision, AuthorizationDecision):
            raise AuthorizationFailure(
                "invalid_authorization_decision",
                "The authorization adapter returned an invalid decision.",
                status=500,
            )
        if policy.mode is AuthorizationMode.CLIENT_GRANT:
            if decision.authorized_result is None:
                raise AuthorizationFailure(
                    "invalid_authorization_decision",
                    "The endpoint requires an authorized result.",
                    status=500,
                )
            authorized_result = decision.authorized_result
            pairing_permit = None
        elif policy.mode is AuthorizationMode.PAIRING:
            if decision.pairing_permit is None:
                raise AuthorizationFailure(
                    "invalid_authorization_decision",
                    "The endpoint requires a pairing permit.",
                    status=500,
                )
            authorized_result = None
            pairing_permit = decision.pairing_permit
        else:
            raise AuthorizationFailure(
                "invalid_authorization_policy",
                "The endpoint policy requires an unsupported authorization mode.",
                status=500,
            )

        raw_state = scope.setdefault("state", {})
        if not isinstance(raw_state, MutableMapping):
            raise AuthorizationFailure(
                "invalid_asgi_scope",
                "The HTTP scope state is not writable.",
                status=500,
            )
        state = cast(MutableMapping[str, object], raw_state)
        state.pop(AUTHORIZATION_RESULT_STATE_KEY, None)
        state.pop(PAIRING_PERMIT_STATE_KEY, None)
        if authorized_result is not None:
            state[AUTHORIZATION_RESULT_STATE_KEY] = authorized_result
        if pairing_permit is not None:
            state[PAIRING_PERMIT_STATE_KEY] = pairing_permit


async def _send_problem(send: ASGISend, problem: AuthorizationProblem) -> None:
    body = json.dumps(
        problem.document(), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    headers: list[tuple[bytes, bytes]] = []
    for name, value in problem.headers.items():
        normalized_name = name.strip().lower()
        if normalized_name in {"content-type", "content-length"}:
            continue
        headers.append((normalized_name.encode("ascii"), value.encode("latin-1")))
    headers.extend(
        [
            (b"content-type", PROBLEM_MEDIA_TYPE.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
    )
    await send(
        {"type": "http.response.start", "status": problem.status, "headers": headers}
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def _authorization_problem_code(error: Exception) -> ProblemCode:
    if isinstance(error, ClientTrustError):
        if error.code in {
            ClientTrustErrorCode.PERMISSION_DENIED,
            ClientTrustErrorCode.SCOPE_MISMATCH,
        }:
            return ProblemCode.PERMISSION_DENIED
        return ProblemCode.TRUST_REQUIRED
    if isinstance(error, AuthorizationFailure):
        if error.status == 403:
            return ProblemCode.PERMISSION_DENIED
        if error.status >= 500:
            return ProblemCode.INTERNAL_ERROR
    return ProblemCode.TRUST_REQUIRED


def _correlation_id(request: HeaderAuthorizationRequest) -> str | None:
    for name, value in request.headers:
        if name.lower() in {b"x-correlation-id", b"x-request-id"}:
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None
