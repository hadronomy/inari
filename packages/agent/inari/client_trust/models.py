from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Self
from urllib.parse import SplitResult, urlsplit, urlunsplit

from .errors import (
    ClientTrustError,
    ClientTrustErrorCode,
    InvalidOriginError,
    ScopeMismatchError,
)
from .permissions import Permission, PermissionCatalog, PermissionSet


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_TOKEN_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,511}\Z")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]{8,512}\Z")


def _identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty stable identifier")


def _token_value(name: str, value: str) -> None:
    if not isinstance(value, str) or not _TOKEN_VALUE.fullmatch(value):
        raise ValueError(f"{name} must be a non-empty token value")


def _jwk_thumbprint(name: str, value: str) -> None:
    if not isinstance(value, str) or not _BASE64URL.fullmatch(value):
        raise ValueError(f"{name} must be a base64url JWK thumbprint")


def _utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    normalized = value.astimezone(UTC)
    return normalized


def _permission_set(value: Any) -> PermissionSet:
    if isinstance(value, (str, Permission)):
        value = (value,)
    return PermissionCatalog.normalize(value)


def _assert_order(first_name: str, first: datetime, second_name: str, second: datetime) -> None:
    if second <= first:
        raise ValueError(f"{second_name} must follow {first_name}")


class PairingRequestState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    CANCELED = "canceled"
    EXPIRED = "expired"
    COMPLETED = "completed"


class PairingLifecycle(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class GrantLifecycle(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class RenewalResultState(StrEnum):
    RENEWED = "renewed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class BoundOrigin:
    """One exact browser origin accepted by an Agent Endpoint."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidOriginError()
        if self.value in {"", "null", "*"} or "*" in self.value:
            raise InvalidOriginError()
        try:
            parsed = urlsplit(self.value)
        except ValueError as exc:
            raise InvalidOriginError("The browser origin is not a valid URL.") from exc
        if parsed.scheme.lower() != "https":
            raise InvalidOriginError("The browser origin must use HTTPS.")
        try:
            hostname = parsed.hostname
            username = parsed.username
            password = parsed.password
        except ValueError as exc:
            raise InvalidOriginError("The browser origin authority is invalid.") from exc
        if not hostname or username or password:
            raise InvalidOriginError()
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise InvalidOriginError("The browser origin must not contain a path or query.")
        host = hostname.lower().rstrip(".")
        if not host or "*" in host or host == "null":
            raise InvalidOriginError()
        if any(char.isspace() or ord(char) < 0x20 for char in host):
            raise InvalidOriginError()
        try:
            port = parsed.port
        except ValueError as exc:
            raise InvalidOriginError("The browser origin has an invalid port.") from exc
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host if port in {None, 443} else f"{host}:{port}"
        normalized = urlunsplit(("https", netloc, "", "", ""))
        object.__setattr__(self, "value", normalized)

    @property
    def scheme(self) -> str:
        return "https"

    @property
    def host(self) -> str:
        return urlsplit(self.value).hostname or ""

    @property
    def port(self) -> int | None:
        return urlsplit(self.value).port

    def matches(self, origin: str | BoundOrigin) -> bool:
        try:
            candidate = origin if isinstance(origin, BoundOrigin) else BoundOrigin(origin)
        except InvalidOriginError:
            return False
        return candidate == self


@dataclass(frozen=True, slots=True)
class BusinessScope:
    """The exact Odoo and Inari tenant scope for one operator session."""

    database: str
    company_id: str
    organization_id: str
    site_id: str
    pos_configuration_id: str | None = None

    def __post_init__(self) -> None:
        _identifier("database", self.database)
        _identifier("company_id", self.company_id)
        _identifier("organization_id", self.organization_id)
        _identifier("site_id", self.site_id)
        if self.pos_configuration_id is not None:
            _identifier("pos_configuration_id", self.pos_configuration_id)

    @property
    def is_pos_scope(self) -> bool:
        return self.pos_configuration_id is not None


@dataclass(frozen=True, slots=True)
class PairingScope:
    """The Agent, origin, and business scope that one Client Pairing binds."""

    agent_id: str
    origin: BoundOrigin
    business: BusinessScope
    audience: str

    def __post_init__(self) -> None:
        _identifier("agent_id", self.agent_id)
        if not isinstance(self.origin, BoundOrigin):
            raise TypeError("origin must be a BoundOrigin")
        if not isinstance(self.business, BusinessScope):
            raise TypeError("business must be a BusinessScope")
        _identifier("audience", self.audience)

    @property
    def database(self) -> str:
        return self.business.database

    @property
    def organization_id(self) -> str:
        return self.business.organization_id

    @property
    def site_id(self) -> str:
        return self.business.site_id

    @property
    def pos_configuration_id(self) -> str | None:
        return self.business.pos_configuration_id


@dataclass(frozen=True, slots=True)
class ClientPairing:
    pairing_id: str
    jwk_thumbprint: str
    scope: PairingScope
    actor_id: str
    role: str
    permissions: PermissionSet
    created_at: datetime
    expires_at: datetime | None
    lifecycle: PairingLifecycle = PairingLifecycle.ACTIVE
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        _identifier("pairing_id", self.pairing_id)
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        if not isinstance(self.scope, PairingScope):
            raise TypeError("scope must be a PairingScope")
        _identifier("actor_id", self.actor_id)
        _identifier("role", self.role)
        object.__setattr__(self, "permissions", _permission_set(self.permissions))
        created_at = _utc("created_at", self.created_at)
        object.__setattr__(self, "created_at", created_at)
        if self.expires_at is not None:
            expires_at = _utc("expires_at", self.expires_at)
            _assert_order("created_at", created_at, "expires_at", expires_at)
            object.__setattr__(self, "expires_at", expires_at)
        if self.last_used_at is not None:
            object.__setattr__(self, "last_used_at", _utc("last_used_at", self.last_used_at))
        if not isinstance(self.lifecycle, PairingLifecycle):
            object.__setattr__(self, "lifecycle", PairingLifecycle(self.lifecycle))

    @property
    def key_thumbprint(self) -> str:
        return self.jwk_thumbprint

    def active_at(self, now: datetime) -> None:
        moment = _utc("now", now)
        if self.lifecycle is PairingLifecycle.REVOKED:
            raise ClientTrustError(ClientTrustErrorCode.GRANT_REVOKED, "The Client Pairing is revoked.")
        if self.lifecycle is PairingLifecycle.EXPIRED or (
            self.expires_at is not None and self.expires_at <= moment
        ):
            raise ClientTrustError(ClientTrustErrorCode.PAIRING_EXPIRED, "The Client Pairing is expired.")


@dataclass(frozen=True, slots=True)
class ClientGrant:
    grant_id: str
    pairing_id: str
    jwk_thumbprint: str
    scope: PairingScope
    actor_id: str
    role: str
    permissions: PermissionSet
    authorization_digest: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    offline_renewal_until: datetime | None = None
    lifecycle: GrantLifecycle = GrantLifecycle.ACTIVE
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("grant_id", self.grant_id),
            ("pairing_id", self.pairing_id),
            ("actor_id", self.actor_id),
            ("role", self.role),
            ("authorization_digest", self.authorization_digest),
            ("token_id", self.token_id),
        ):
            _identifier(name, value)
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        if not isinstance(self.scope, PairingScope):
            raise TypeError("scope must be a PairingScope")
        object.__setattr__(self, "permissions", _permission_set(self.permissions))
        issued_at = _utc("issued_at", self.issued_at)
        expires_at = _utc("expires_at", self.expires_at)
        _assert_order("issued_at", issued_at, "expires_at", expires_at)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        if self.offline_renewal_until is not None:
            renewal_until = _utc("offline_renewal_until", self.offline_renewal_until)
            _assert_order("expires_at", expires_at, "offline_renewal_until", renewal_until)
            object.__setattr__(self, "offline_renewal_until", renewal_until)
        if self.last_used_at is not None:
            object.__setattr__(self, "last_used_at", _utc("last_used_at", self.last_used_at))
        if not isinstance(self.lifecycle, GrantLifecycle):
            object.__setattr__(self, "lifecycle", GrantLifecycle(self.lifecycle))

    @property
    def key_thumbprint(self) -> str:
        return self.jwk_thumbprint

    @property
    def client_pairing_id(self) -> str:
        return self.pairing_id

    def active_at(self, now: datetime) -> None:
        moment = _utc("now", now)
        if self.lifecycle is GrantLifecycle.REVOKED:
            raise ClientTrustError(ClientTrustErrorCode.GRANT_REVOKED, "The Client Grant is revoked.")
        if self.lifecycle is GrantLifecycle.EXPIRED or self.expires_at <= moment:
            raise ClientTrustError(ClientTrustErrorCode.GRANT_EXPIRED, "The Client Grant is expired.")


@dataclass(frozen=True, slots=True)
class RequestTarget:
    method: str
    uri: str

    def __post_init__(self) -> None:
        method = self.method.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,19}", method):
            raise ValueError("method must be an HTTP method")
        object.__setattr__(self, "method", method)
        parsed = _https_url("uri", self.uri, allow_path=True)
        origin = BoundOrigin(urlunsplit(("https", parsed.netloc, "", "", "")))
        normalized = urlunsplit(
            ("https", urlsplit(origin.value).netloc, parsed.path or "/", "", "")
        )
        object.__setattr__(self, "uri", normalized)

    @property
    def htm(self) -> str:
        return self.method

    @property
    def htu(self) -> str:
        return self.uri


def _https_url(name: str, value: str, *, allow_path: bool) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError(f"{name} must be an absolute HTTPS URL")
    if parsed.username or parsed.password or (not allow_path and parsed.path not in {"", "/"}):
        raise ValueError(f"{name} contains unsupported URL components")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{name} has an invalid port") from exc
    return parsed


@dataclass(frozen=True, slots=True)
class EndpointPolicy:
    agent_id: str
    audience: str
    origin: BoundOrigin
    business: BusinessScope
    allowed_methods: frozenset[str] = frozenset({"GET", "POST", "DELETE"})
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _identifier("agent_id", self.agent_id)
        _identifier("audience", self.audience)
        if not isinstance(self.origin, BoundOrigin):
            raise TypeError("origin must be a BoundOrigin")
        if not isinstance(self.business, BusinessScope):
            raise TypeError("business must be a BusinessScope")
        methods = frozenset(method.upper() for method in self.allowed_methods)
        if any(not re.fullmatch(r"[A-Z][A-Z0-9-]{0,19}", method) for method in methods):
            raise ValueError("allowed_methods must contain HTTP methods")
        object.__setattr__(self, "allowed_methods", methods)
        paths = tuple(self.allowed_paths)
        if any(not path.startswith("/") or "?" in path or "#" in path for path in paths):
            raise ValueError("allowed_paths must contain path-only values")
        object.__setattr__(self, "allowed_paths", paths)

    def accepts(self, target: RequestTarget) -> None:
        if target.method not in self.allowed_methods:
            raise ScopeMismatchError("The request method is not allowed for this Agent Endpoint.")
        target_origin = BoundOrigin(urlunsplit(("https", urlsplit(target.uri).netloc, "", "", "")))
        if target_origin != self.origin:
            raise ScopeMismatchError("The request origin does not match the Client Pairing.")
        path = urlsplit(target.uri).path or "/"
        if self.allowed_paths and path not in self.allowed_paths:
            raise ScopeMismatchError("The request path is not allowed for this Agent Endpoint.")


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    issuer: str
    subject: str
    audience: str
    token_id: str
    issued_at: datetime
    expires_at: datetime
    cnf_jkt: str
    client_pairing_id: str
    client_grant_id: str
    business: BusinessScope
    permissions: PermissionSet

    def __post_init__(self) -> None:
        for name, value in (
            ("issuer", self.issuer),
            ("subject", self.subject),
            ("audience", self.audience),
            ("token_id", self.token_id),
            ("client_pairing_id", self.client_pairing_id),
            ("client_grant_id", self.client_grant_id),
        ):
            _identifier(name, value)
        _jwk_thumbprint("cnf_jkt", self.cnf_jkt)
        if not isinstance(self.business, BusinessScope):
            raise TypeError("business must be a BusinessScope")
        object.__setattr__(self, "permissions", _permission_set(self.permissions))
        issued_at = _utc("issued_at", self.issued_at)
        expires_at = _utc("expires_at", self.expires_at)
        _assert_order("issued_at", issued_at, "expires_at", expires_at)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def iss(self) -> str:
        return self.issuer

    @property
    def sub(self) -> str:
        return self.subject

    @property
    def aud(self) -> str:
        return self.audience

    @property
    def jti(self) -> str:
        return self.token_id

    @property
    def jkt(self) -> str:
        return self.cnf_jkt

    @property
    def iat(self) -> datetime:
        return self.issued_at

    @property
    def exp(self) -> datetime:
        return self.expires_at

    def claims(self) -> Mapping[str, Any]:
        values: dict[str, Any] = {
            "iss": self.issuer,
            "sub": self.subject,
            "aud": self.audience,
            "jti": self.token_id,
            "iat": int(self.issued_at.timestamp()),
            "exp": int(self.expires_at.timestamp()),
            "cnf": MappingProxyType({"jkt": self.cnf_jkt}),
            "pairing_id": self.client_pairing_id,
            "grant_id": self.client_grant_id,
            "database": self.business.database,
            "company_id": self.business.company_id,
            "organization_id": self.business.organization_id,
            "site_id": self.business.site_id,
            "scope": tuple(sorted(permission.value for permission in self.permissions)),
        }
        if self.business.pos_configuration_id is not None:
            values["pos_configuration_id"] = self.business.pos_configuration_id
        return MappingProxyType(values)


@dataclass(frozen=True, slots=True)
class AcceptedDPoPProof:
    jwk_thumbprint: str
    htm: str
    htu: str
    iat: datetime
    ath: str
    nonce: str
    jti: str
    accepted_at: datetime

    def __post_init__(self) -> None:
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        target = RequestTarget(self.htm, self.htu)
        object.__setattr__(self, "htm", target.htm)
        object.__setattr__(self, "htu", target.htu)
        if not _BASE64URL.fullmatch(self.ath):
            raise ValueError("ath must be a base64url access-token hash")
        _token_value("nonce", self.nonce)
        _token_value("jti", self.jti)
        object.__setattr__(self, "iat", _utc("iat", self.iat))
        object.__setattr__(self, "accepted_at", _utc("accepted_at", self.accepted_at))

    @property
    def key_thumbprint(self) -> str:
        return self.jwk_thumbprint

    @property
    def target(self) -> RequestTarget:
        return RequestTarget(self.htm, self.htu)


@dataclass(frozen=True, slots=True)
class GrantAdmissionProof:
    pairing_request_id: str
    assertion_jti: str
    assertion_digest: str
    jwk_thumbprint: str
    admitted_at: datetime

    def __post_init__(self) -> None:
        _identifier("pairing_request_id", self.pairing_request_id)
        _token_value("assertion_jti", self.assertion_jti)
        _identifier("assertion_digest", self.assertion_digest)
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        object.__setattr__(self, "admitted_at", _utc("admitted_at", self.admitted_at))


@dataclass(frozen=True, slots=True)
class AuthorizedRequest:
    target: RequestTarget
    grant: ClientGrant
    dpop: AcceptedDPoPProof
    endpoint: EndpointPolicy
    accepted_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.target, RequestTarget):
            raise TypeError("target must be a RequestTarget")
        if not isinstance(self.grant, ClientGrant):
            raise TypeError("grant must be a ClientGrant")
        if not isinstance(self.dpop, AcceptedDPoPProof):
            raise TypeError("dpop must be an AcceptedDPoPProof")
        if not isinstance(self.endpoint, EndpointPolicy):
            raise TypeError("endpoint must be an EndpointPolicy")
        if (
            self.grant.scope.agent_id != self.endpoint.agent_id
            or self.grant.scope.audience != self.endpoint.audience
            or self.grant.scope.origin != self.endpoint.origin
            or self.grant.scope.business != self.endpoint.business
        ):
            raise ScopeMismatchError("The Client Grant does not match the Agent Endpoint.")
        if self.dpop.target != self.target:
            raise ScopeMismatchError("The DPoP proof does not match the request target.")
        if self.dpop.jwk_thumbprint != self.grant.jwk_thumbprint:
            raise ScopeMismatchError("The DPoP key does not match the Client Grant.")
        self.endpoint.accepts(self.target)
        object.__setattr__(self, "accepted_at", _utc("accepted_at", self.accepted_at))

    def require(self, permission: Permission | str) -> Self:
        PermissionCatalog.require(self.grant.permissions, permission)
        return self


@dataclass(frozen=True, slots=True)
class PairingAssertionClaims:
    issuer: str
    subject: str
    audience: str
    pairing_request_id: str
    agent_id: str
    jwk_thumbprint: str
    business: BusinessScope
    actor_id: str
    role: str
    scopes: PermissionSet
    session_nonce: str
    issued_at: datetime
    expires_at: datetime
    jti: str

    def __post_init__(self) -> None:
        for name, value in (
            ("issuer", self.issuer),
            ("subject", self.subject),
            ("audience", self.audience),
            ("pairing_request_id", self.pairing_request_id),
            ("agent_id", self.agent_id),
            ("actor_id", self.actor_id),
            ("role", self.role),
        ):
            _identifier(name, value)
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        if not isinstance(self.business, BusinessScope):
            raise TypeError("business must be a BusinessScope")
        object.__setattr__(self, "scopes", _permission_set(self.scopes))
        _token_value("session_nonce", self.session_nonce)
        issued_at = _utc("issued_at", self.issued_at)
        expires_at = _utc("expires_at", self.expires_at)
        _assert_order("issued_at", issued_at, "expires_at", expires_at)
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)
        _token_value("jti", self.jti)

    @property
    def iss(self) -> str:
        return self.issuer

    @property
    def sub(self) -> str:
        return self.subject

    @property
    def aud(self) -> str:
        return self.audience

    @property
    def iat(self) -> datetime:
        return self.issued_at

    @property
    def exp(self) -> datetime:
        return self.expires_at


PairingAssertionValues = PairingAssertionClaims


@dataclass(frozen=True, slots=True)
class PairingAssertion:
    claims: PairingAssertionClaims
    compact_jws: str
    signer_key_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.claims, PairingAssertionClaims):
            raise TypeError("claims must be PairingAssertionClaims")
        if self.compact_jws.count(".") != 2:
            raise ValueError("compact_jws must contain three encoded segments")
        _identifier("signer_key_id", self.signer_key_id)


@dataclass(frozen=True, slots=True)
class PairingRequest:
    request_id: str
    scope: PairingScope
    browser_jwk_thumbprint: str
    requested_permissions: PermissionSet
    session_nonce: str
    phrase: str
    created_at: datetime
    expires_at: datetime
    state: PairingRequestState = PairingRequestState.PENDING

    def __post_init__(self) -> None:
        _identifier("request_id", self.request_id)
        if not isinstance(self.scope, PairingScope):
            raise TypeError("scope must be a PairingScope")
        _jwk_thumbprint("browser_jwk_thumbprint", self.browser_jwk_thumbprint)
        object.__setattr__(self, "requested_permissions", _permission_set(self.requested_permissions))
        _token_value("session_nonce", self.session_nonce)
        if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2}", self.phrase):
            raise ValueError("phrase must contain three hyphen-separated words")
        created_at = _utc("created_at", self.created_at)
        expires_at = _utc("expires_at", self.expires_at)
        _assert_order("created_at", created_at, "expires_at", expires_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        if not isinstance(self.state, PairingRequestState):
            object.__setattr__(self, "state", PairingRequestState(self.state))


@dataclass(frozen=True, slots=True)
class PairingCommand:
    request: PairingRequest
    assertion: PairingAssertion

    def __post_init__(self) -> None:
        if not isinstance(self.request, PairingRequest):
            raise TypeError("request must be a PairingRequest")
        if not isinstance(self.assertion, PairingAssertion):
            raise TypeError("assertion must be a PairingAssertion")
        claims = self.assertion.claims
        if claims.pairing_request_id != self.request.request_id:
            raise ScopeMismatchError("The Pairing Assertion does not match the request.")
        if claims.jwk_thumbprint != self.request.browser_jwk_thumbprint:
            raise ScopeMismatchError("The Pairing Assertion does not match the browser key.")
        if (
            claims.agent_id != self.request.scope.agent_id
            or claims.audience != self.request.scope.audience
            or claims.business != self.request.scope.business
            or claims.scopes != self.request.requested_permissions
            or claims.session_nonce != self.request.session_nonce
        ):
            raise ScopeMismatchError("The Pairing Assertion does not match the request scope.")


PairClientCommand = PairingCommand


@dataclass(frozen=True, slots=True)
class PairingResult:
    pairing: ClientPairing
    grant: ClientGrant
    admission: GrantAdmissionProof


PairClientResult = PairingResult


@dataclass(frozen=True, slots=True)
class RenewalCommand:
    pairing_id: str
    grant_id: str
    jwk_thumbprint: str
    session_nonce: str
    requested_at: datetime

    def __post_init__(self) -> None:
        _identifier("pairing_id", self.pairing_id)
        _identifier("grant_id", self.grant_id)
        _jwk_thumbprint("jwk_thumbprint", self.jwk_thumbprint)
        _token_value("session_nonce", self.session_nonce)
        object.__setattr__(self, "requested_at", _utc("requested_at", self.requested_at))


GrantRenewalCommand = RenewalCommand


@dataclass(frozen=True, slots=True)
class RenewalResult:
    grant: ClientGrant
    claims: AccessTokenClaims
    state: RenewalResultState = RenewalResultState.RENEWED

    def __post_init__(self) -> None:
        if not isinstance(self.grant, ClientGrant):
            raise TypeError("grant must be a ClientGrant")
        if not isinstance(self.claims, AccessTokenClaims):
            raise TypeError("claims must be AccessTokenClaims")
        if self.grant.jwk_thumbprint != self.claims.cnf_jkt:
            raise ScopeMismatchError("The renewed token does not match the Client Grant.")
        if (
            self.claims.client_pairing_id != self.grant.pairing_id
            or self.claims.client_grant_id != self.grant.grant_id
            or self.claims.business != self.grant.scope.business
            or self.claims.permissions != self.grant.permissions
        ):
            raise ScopeMismatchError("The renewed token does not match the Client Grant scope.")
        if not isinstance(self.state, RenewalResultState):
            object.__setattr__(self, "state", RenewalResultState(self.state))


GrantRenewalResult = RenewalResult


__all__ = [
    "AccessTokenClaims",
    "AcceptedDPoPProof",
    "AuthorizedRequest",
    "BoundOrigin",
    "BusinessScope",
    "ClientGrant",
    "ClientPairing",
    "EndpointPolicy",
    "GrantAdmissionProof",
    "GrantLifecycle",
    "GrantRenewalCommand",
    "GrantRenewalResult",
    "PairClientCommand",
    "PairClientResult",
    "PairingAssertion",
    "PairingAssertionClaims",
    "PairingAssertionValues",
    "PairingCommand",
    "PairingLifecycle",
    "PairingRequest",
    "PairingRequestState",
    "PairingResult",
    "PairingScope",
    "RenewalCommand",
    "RenewalResult",
    "RenewalResultState",
    "RequestTarget",
]
