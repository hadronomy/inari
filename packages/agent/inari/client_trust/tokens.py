"""Signed Client Grant credentials and RFC 9449 DPoP verification."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from typing import Any, Final

from joserfc import jwt

from .crypto import _decode_jws, _ensure_public_ed25519, _invalid, _private_ed25519
from .errors import ClientTrustError, ClientTrustErrorCode
from .models import AccessTokenClaims, AcceptedDPoPProof, ClientGrant, RequestTarget


_ACCESS_TOKEN_TYPE: Final = "at+jwt"
_DPOP_TYPE: Final = "dpop+jwt"
_ALGORITHM: Final = "Ed25519"
_DPOP_WINDOW: Final = timedelta(minutes=2)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _required_string(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise _invalid(
            ClientTrustErrorCode.INVALID_VALUE,
            f"The access credential has an invalid {name} claim.",
        )
    return value


def _required_integer(claims: Mapping[str, Any], name: str) -> int:
    value = claims.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _invalid(
            ClientTrustErrorCode.INVALID_VALUE,
            f"The signed value has an invalid {name} claim.",
        )
    return value


def _access_payload(
    grant: ClientGrant,
    *,
    issuer: str,
    audience: str,
    token_id: str,
    issued_at: datetime,
    expires_at: datetime,
    generation: int,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "iss": issuer,
        "sub": grant.actor_id,
        "aud": audience,
        "jti": token_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "cnf": {"jkt": grant.jwk_thumbprint},
        "pairing_id": grant.pairing_id,
        "grant_id": grant.grant_id,
        "database": grant.scope.business.database,
        "company_id": grant.scope.business.company_id,
        "organization_id": grant.scope.business.organization_id,
        "site_id": grant.scope.business.site_id,
        "scope": sorted(permission.value for permission in grant.permissions),
        "generation": generation,
        "authorization_digest": grant.authorization_digest,
    }
    if grant.scope.business.pos_configuration_id is not None:
        values["pos_configuration_id"] = grant.scope.business.pos_configuration_id
    return values


class AccessTokenSigner:
    """Issues short-lived access credentials bound to one Client Grant."""

    def __init__(
        self,
        *,
        signing_key: object,
        issuer: str,
        audience: str,
        generation: int = 0,
    ) -> None:
        self._key = _private_ed25519(signing_key)
        if not isinstance(issuer, str) or not issuer:
            raise ValueError("issuer must be non-empty")
        if not isinstance(audience, str) or not audience:
            raise ValueError("audience must be non-empty")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        self.issuer = issuer
        self.audience = audience
        self.generation = generation

    def issue(
        self,
        grant: ClientGrant,
        *,
        issued_at: datetime,
        expires_at: datetime,
        token_id: str | None = None,
        generation: int | None = None,
    ) -> tuple[str, AccessTokenClaims]:
        if not isinstance(grant, ClientGrant):
            raise TypeError("grant must be a ClientGrant")
        issued_at = _utc(issued_at, "issued_at")
        expires_at = _utc(expires_at, "expires_at")
        if expires_at <= issued_at:
            raise ValueError("expires_at must follow issued_at")
        actual_generation = self.generation if generation is None else generation
        if (
            not isinstance(actual_generation, int)
            or isinstance(actual_generation, bool)
            or actual_generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        token_id = token_id or _b64url(
            sha256(f"{grant.grant_id}:{issued_at.timestamp()}".encode()).digest()[:18]
        )
        claims_payload = _access_payload(
            grant,
            issuer=self.issuer,
            audience=self.audience,
            token_id=token_id,
            issued_at=issued_at,
            expires_at=expires_at,
            generation=actual_generation,
        )
        token = jwt.encode(
            {"alg": _ALGORITHM, "typ": _ACCESS_TOKEN_TYPE},
            claims_payload,
            self._key,
            algorithms=[_ALGORITHM],
        )
        claims = _access_claims(claims_payload)
        return token, claims


class AccessTokenVerifier:
    """Verifies Agent access credentials and returns content-free claims."""

    def __init__(
        self,
        *,
        verification_key: object,
        issuer: str,
        audience: str,
        generation: int | None = None,
        authorization_digest: str | None = None,
    ) -> None:
        self._key, _ = _ensure_public_ed25519(
            verification_key, code=ClientTrustErrorCode.INVALID_VALUE
        )
        self.issuer = issuer
        self.audience = audience
        self.generation = generation
        self.authorization_digest = authorization_digest

    def verify(self, token: str, *, at: datetime) -> AccessTokenClaims:
        if not isinstance(token, str):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE, "The access credential is invalid."
            )
        header = _header(token, code=ClientTrustErrorCode.INVALID_VALUE)
        if (
            set(header) != {"typ", "alg"}
            or header.get("alg") != _ALGORITHM
            or header.get("typ") != _ACCESS_TOKEN_TYPE
        ):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential type is invalid.",
            )
        parsed = _decode_jws(token, self._key, code=ClientTrustErrorCode.INVALID_VALUE)
        claims = parsed.claims
        if not isinstance(claims, Mapping):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential payload is invalid.",
            )
        allowed = frozenset(
            {
                "iss",
                "sub",
                "aud",
                "jti",
                "iat",
                "exp",
                "cnf",
                "pairing_id",
                "grant_id",
                "database",
                "company_id",
                "organization_id",
                "site_id",
                "pos_configuration_id",
                "scope",
                "generation",
                "authorization_digest",
            }
        )
        if set(claims) - allowed:
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential contains unsupported claims.",
            )
        if (
            _required_string(claims, "iss") != self.issuer
            or _required_string(claims, "aud") != self.audience
        ):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential scope is invalid.",
            )
        issued_at = datetime.fromtimestamp(_required_integer(claims, "iat"), tz=UTC)
        expires_at = datetime.fromtimestamp(_required_integer(claims, "exp"), tz=UTC)
        moment = _utc(at, "at")
        if expires_at <= moment or issued_at > moment + _DPOP_WINDOW:
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential is outside its validity period.",
            )
        generation = _required_integer(claims, "generation")
        if self.generation is not None and generation != self.generation:
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential generation is stale.",
            )
        authorization_digest = _required_string(claims, "authorization_digest")
        if (
            self.authorization_digest is not None
            and authorization_digest != self.authorization_digest
        ):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential authorization is stale.",
            )
        cnf = claims.get("cnf")
        if not isinstance(cnf, Mapping) or set(cnf) != {"jkt"}:
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential confirmation is invalid.",
            )
        jkt = _required_string(cnf, "jkt")
        scope = claims.get("scope")
        if not isinstance(scope, list) or any(
            not isinstance(item, str) for item in scope
        ):
            raise _invalid(
                ClientTrustErrorCode.INVALID_VALUE,
                "The access credential scope is invalid.",
            )
        return _access_claims(
            claims, issued_at=issued_at, expires_at=expires_at, jkt=jkt
        )


def _access_claims(
    claims: Mapping[str, Any],
    *,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
    jkt: str | None = None,
) -> AccessTokenClaims:
    from .models import BusinessScope
    from .permissions import PermissionCatalog

    code = ClientTrustErrorCode.INVALID_VALUE
    issued = issued_at or datetime.fromtimestamp(
        _required_integer(claims, "iat"), tz=UTC
    )
    expires = expires_at or datetime.fromtimestamp(
        _required_integer(claims, "exp"), tz=UTC
    )
    try:
        business = BusinessScope(
            database=_required_string(claims, "database"),
            company_id=_required_string(claims, "company_id"),
            organization_id=_required_string(claims, "organization_id"),
            site_id=_required_string(claims, "site_id"),
            pos_configuration_id=claims.get("pos_configuration_id"),
        )
        permissions = PermissionCatalog.normalize(claims.get("scope", []))
        kwargs: dict[str, Any] = {
            "issuer": _required_string(claims, "iss"),
            "subject": _required_string(claims, "sub"),
            "audience": _required_string(claims, "aud"),
            "token_id": _required_string(claims, "jti"),
            "issued_at": issued,
            "expires_at": expires,
            "cnf_jkt": jkt or _required_string(claims.get("cnf", {}), "jkt"),
            "client_pairing_id": _required_string(claims, "pairing_id"),
            "client_grant_id": _required_string(claims, "grant_id"),
            "business": business,
            "permissions": permissions,
        }
        return AccessTokenClaims(
            **kwargs,
            generation=_required_integer(claims, "generation"),
            authorization_digest=_required_string(claims, "authorization_digest"),
        )
    except (TypeError, ValueError, ClientTrustError) as exc:
        if isinstance(exc, ClientTrustError):
            raise
        raise _invalid(code, "The access credential claims are invalid.") from exc


class DPoPProofVerifier:
    """Verifies the exact method, URI, nonce, and token binding in a DPoP proof."""

    def verify(
        self,
        proof: str,
        *,
        target: RequestTarget,
        claims: AccessTokenClaims,
        access_token: str,
        nonce: str,
        at: datetime,
    ) -> AcceptedDPoPProof:
        code = ClientTrustErrorCode.INVALID_DPOP_PROOF
        if not isinstance(target, RequestTarget) or not isinstance(
            claims, AccessTokenClaims
        ):
            raise _invalid(code, "The DPoP request context is invalid.")
        header = _header(proof, code=code)
        if (
            set(header) != {"typ", "alg", "jwk"}
            or header.get("typ") != _DPOP_TYPE
            or header.get("alg") != _ALGORITHM
        ):
            raise _invalid(code, "The DPoP protected header is invalid.")
        key, public_jwk = _ensure_public_ed25519(header.get("jwk"), code=code)
        parsed = _decode_jws(proof, key, code=code)
        payload = parsed.claims
        if not isinstance(payload, Mapping):
            raise _invalid(code, "The DPoP payload is invalid.")
        if set(payload) != {"htm", "htu", "iat", "ath", "nonce", "jti"}:
            raise _invalid(code, "The DPoP payload has unsupported claims.")
        htm = payload.get("htm")
        htu = payload.get("htu")
        if (
            not isinstance(htm, str)
            or htm.upper() != target.method
            or htm != target.method
        ):
            raise _invalid(code, "The DPoP method does not match the request.")
        if not isinstance(htu, str) or htu != target.htu:
            raise _invalid(code, "The DPoP URI does not match the request.")
        issued_at = datetime.fromtimestamp(_required_integer(payload, "iat"), tz=UTC)
        moment = _utc(at, "at")
        if abs(moment - issued_at) > _DPOP_WINDOW:
            raise _invalid(code, "The DPoP proof is outside the clock window.")
        if not isinstance(nonce, str) or not nonce or payload.get("nonce") != nonce:
            raise _invalid(code, "The DPoP nonce is invalid.")
        access_hash = _b64url(sha256(access_token.encode("utf-8")).digest())
        if not isinstance(payload.get("ath"), str) or not compare_digest(
            payload["ath"], access_hash
        ):
            raise _invalid(code, "The DPoP access-token binding is invalid.")
        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            raise _invalid(code, "The DPoP replay identity is invalid.")
        thumbprint = _b64url(sha256(_canonical_jwk(public_jwk)).digest())
        if thumbprint != claims.cnf_jkt:
            raise _invalid(code, "The DPoP key does not match the access credential.")
        return AcceptedDPoPProof(
            jwk_thumbprint=thumbprint,
            htm=target.method,
            htu=target.htu,
            iat=issued_at,
            ath=access_hash,
            nonce=nonce,
            jti=jti,
            accepted_at=moment,
        )


def _canonical_jwk(public_jwk: Mapping[str, Any]) -> bytes:
    # RFC 7638 uses the lexicographically ordered required members only.
    import json

    return json.dumps(
        {name: public_jwk[name] for name in ("crv", "kty", "x")},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _header(value: str, *, code: ClientTrustErrorCode) -> Mapping[str, Any]:
    if not isinstance(value, str) or value.count(".") != 2:
        raise _invalid(code, "The signed value is not a compact JWS.")
    import json

    try:
        raw = base64.urlsafe_b64decode(value.split(".", 1)[0] + "===")
        header = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise _invalid(
            code, "The signed value has an invalid protected header."
        ) from exc
    if not isinstance(header, Mapping):
        raise _invalid(code, "The signed value has an invalid protected header.")
    return header


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = ["AccessTokenSigner", "AccessTokenVerifier", "DPoPProofVerifier"]
