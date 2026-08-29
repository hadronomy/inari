"""Ed25519 and compact-JWS primitives for Client Trust.

This module keeps JOSE details at the trust boundary.  Domain services receive
validated model values and never need to inspect a JWT dictionary.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from joserfc import jwk, jwt
from joserfc.jwk import OKPKey

from .errors import ClientTrustError, ClientTrustErrorCode
from .models import PairingAssertion, PairingAssertionClaims, PairingRequest


_ALGORITHM: Final = "Ed25519"
_DPOP_TYPE: Final = "dpop+jwt"
_ASSERTION_TYPE: Final = "application/inari-pairing-assertion+jws"
_MAX_CLOCK_SKEW: Final = timedelta(seconds=120)
_PUBLIC_JWK_FIELDS: Final = frozenset({"kty", "crv", "x"})
_OPTIONAL_JWK_FIELDS: Final = frozenset({"alg", "kid", "key_ops", "use"})


def _invalid(code: ClientTrustErrorCode, message: str) -> ClientTrustError:
    return ClientTrustError(code, message)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _as_mapping(
    value: object, *, code: ClientTrustErrorCode, message: str
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid(code, message)
    return value


def _string(claims: Mapping[str, Any], name: str, *, code: ClientTrustErrorCode) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value:
        raise _invalid(code, f"The signed value has an invalid {name} claim.")
    return value


def _integer(
    claims: Mapping[str, Any], name: str, *, code: ClientTrustErrorCode
) -> int:
    value = claims.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise _invalid(code, f"The signed value has an invalid {name} claim.")
    return value


def _datetime_claim(
    claims: Mapping[str, Any], name: str, *, code: ClientTrustErrorCode
) -> datetime:
    return datetime.fromtimestamp(_integer(claims, name, code=code), tz=UTC)


def _ensure_public_ed25519(
    value: object, *, code: ClientTrustErrorCode
) -> tuple[OKPKey, dict[str, Any]]:
    """Return a strict public Ed25519 key and its public JWK representation."""

    if isinstance(value, OKPKey):
        try:
            data = value.as_dict(private=False)
        except Exception as exc:  # pragma: no cover - defensive JOSE boundary
            raise _invalid(code, "The Ed25519 key is invalid.") from exc
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        raise _invalid(code, "The Ed25519 key is invalid.")

    if not isinstance(data, dict):
        raise _invalid(code, "The Ed25519 key is invalid.")
    if data.get("kty") != "OKP" or data.get("crv") != "Ed25519":
        raise _invalid(code, "Only public Ed25519 JWKs are accepted.")
    if "d" in data:
        raise _invalid(code, "A public JWK cannot contain private key material.")
    if set(data) - (_PUBLIC_JWK_FIELDS | _OPTIONAL_JWK_FIELDS):
        raise _invalid(code, "The Ed25519 JWK contains unsupported members.")
    if set(data) != _PUBLIC_JWK_FIELDS and not _PUBLIC_JWK_FIELDS.issubset(data):
        raise _invalid(code, "The Ed25519 JWK is incomplete.")
    x = data.get("x")
    if not isinstance(x, str) or not x or "=" in x or _b64url_decode(x) is None:
        raise _invalid(code, "The Ed25519 JWK has an invalid public key.")
    try:
        key = jwk.import_key(data, key_type="OKP")
        public = dict(key.as_dict())
    except Exception as exc:  # pragma: no cover - exact library errors vary
        raise _invalid(code, "The Ed25519 JWK is invalid.") from exc
    if key.is_private:
        raise _invalid(code, "A public JWK cannot contain private key material.")
    # Metadata does not participate in RFC 7638.  Keep it only when a caller
    # supplied it so the protected JWK remains useful for key selection.
    public.update({name: data[name] for name in _OPTIONAL_JWK_FIELDS if name in data})
    return key, public


def _b64url_decode(value: str) -> bytes | None:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        return None
    if _b64url(decoded) != value or len(decoded) != 32:
        return None
    return decoded


def public_ed25519_jwk(value: object) -> dict[str, Any]:
    """Validate and return a public Ed25519 JWK."""

    return _ensure_public_ed25519(value, code=ClientTrustErrorCode.INVALID_VALUE)[1]


def jwk_thumbprint(value: object) -> str:
    """Return the RFC 7638 SHA-256 thumbprint for an Ed25519 public JWK."""

    key, _ = _ensure_public_ed25519(value, code=ClientTrustErrorCode.INVALID_VALUE)
    return key.thumbprint()


def _private_ed25519(value: object) -> OKPKey:
    if not isinstance(value, (OKPKey, Mapping)):
        raise _invalid(
            ClientTrustErrorCode.INVALID_VALUE, "The Ed25519 signing key is invalid."
        )
    try:
        data = value.as_dict(private=True) if isinstance(value, OKPKey) else dict(value)
        if data.get("kty") != "OKP" or data.get("crv") != "Ed25519" or "d" not in data:
            raise ValueError("not a private Ed25519 key")
        key = jwk.import_key(data, key_type="OKP")
    except Exception as exc:
        raise _invalid(
            ClientTrustErrorCode.INVALID_VALUE, "The Ed25519 signing key is invalid."
        ) from exc
    if not key.is_private:
        raise _invalid(
            ClientTrustErrorCode.INVALID_VALUE,
            "The Ed25519 signing key is not private.",
        )
    return key


def _decode_jws(value: str, key: OKPKey, *, code: ClientTrustErrorCode) -> jwt.Token:
    if not isinstance(value, str) or value.count(".") != 2:
        raise _invalid(code, "The signed value is not a compact JWS.")
    try:
        token = jwt.decode(value, key, algorithms=[_ALGORITHM])
    except Exception as exc:  # pragma: no cover - exact library errors vary
        raise _invalid(code, "The signed value has an invalid signature.") from exc
    if not isinstance(token.header, Mapping):
        raise _invalid(code, "The signed value has an invalid protected header.")
    if token.header.get("alg") != _ALGORITHM:
        raise _invalid(code, "The signed value uses an unsupported algorithm.")
    if token.header.get("crit") is not None:
        raise _invalid(code, "The signed value uses unsupported critical headers.")
    return token


def _claim_names(
    claims: Mapping[str, Any], allowed: frozenset[str], *, code: ClientTrustErrorCode
) -> None:
    if set(claims) - allowed:
        raise _invalid(code, "The signed value contains unsupported claims.")


def _pairing_claims(claims: Mapping[str, Any]) -> PairingAssertionClaims:
    code = ClientTrustErrorCode.INVALID_ASSERTION
    allowed = frozenset(
        {
            "iss",
            "sub",
            "aud",
            "iat",
            "exp",
            "jti",
            "pairing_request_id",
            "agent_id",
            "jwk_thumbprint",
            "database",
            "company_id",
            "organization_id",
            "site_id",
            "pos_configuration_id",
            "actor_id",
            "role",
            "scopes",
            "session_nonce",
        }
    )
    _claim_names(claims, allowed, code=code)
    standard = {
        name: _string(claims, name, code=code) for name in ("iss", "sub", "aud", "jti")
    }
    issued_at = _datetime_claim(claims, "iat", code=code)
    expires_at = _datetime_claim(claims, "exp", code=code)
    scopes = claims.get("scopes")
    if not isinstance(scopes, list) or any(
        not isinstance(scope, str) for scope in scopes
    ):
        raise _invalid(code, "The signed value has invalid scopes.")
    try:
        from .models import BusinessScope
        from .permissions import PermissionCatalog

        pos_configuration_id = claims.get("pos_configuration_id")
        if pos_configuration_id is not None and not isinstance(
            pos_configuration_id, str
        ):
            raise ValueError("pos_configuration_id must be a string")
        business = BusinessScope(
            database=_string(claims, "database", code=code),
            company_id=_string(claims, "company_id", code=code),
            organization_id=_string(claims, "organization_id", code=code),
            site_id=_string(claims, "site_id", code=code),
            pos_configuration_id=pos_configuration_id,
        )
        return PairingAssertionClaims(
            issuer=standard["iss"],
            subject=standard["sub"],
            audience=standard["aud"],
            pairing_request_id=_string(claims, "pairing_request_id", code=code),
            agent_id=_string(claims, "agent_id", code=code),
            jwk_thumbprint=_string(claims, "jwk_thumbprint", code=code),
            business=business,
            actor_id=_string(claims, "actor_id", code=code),
            role=_string(claims, "role", code=code),
            scopes=PermissionCatalog.normalize(scopes),
            session_nonce=_string(claims, "session_nonce", code=code),
            issued_at=issued_at,
            expires_at=expires_at,
            jti=standard["jti"],
        )
    except (TypeError, ValueError, ClientTrustError) as exc:
        raise _invalid(code, "The Pairing Assertion claims are invalid.") from exc


class PairingAssertionSigner:
    """Signs the company-issued Pairing Assertion compact JWS."""

    def __init__(self, *, signing_key: object, signer_key_id: str) -> None:
        self._key = _private_ed25519(signing_key)
        if not isinstance(signer_key_id, str) or not signer_key_id:
            raise ValueError("signer_key_id must be non-empty")
        self._signer_key_id = signer_key_id

    def sign(self, claims: PairingAssertionClaims) -> PairingAssertion:
        if not isinstance(claims, PairingAssertionClaims):
            raise TypeError("claims must be PairingAssertionClaims")
        payload = dict(_pairing_payload(claims))
        compact = jwt.encode(
            {"alg": _ALGORITHM, "typ": _ASSERTION_TYPE, "kid": self._signer_key_id},
            payload,
            self._key,
            algorithms=[_ALGORITHM],
        )
        return PairingAssertion(
            claims=claims, compact_jws=compact, signer_key_id=self._signer_key_id
        )


class PairingAssertionVerifier:
    """Verifies a Pairing Assertion against the current Odoo signing keys."""

    def __init__(
        self,
        *,
        verification_keys: Mapping[str, object],
        issuer: str | None = None,
        audience: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._keys = {
            kid: _ensure_public_ed25519(
                key, code=ClientTrustErrorCode.INVALID_ASSERTION
            )[0]
            for kid, key in verification_keys.items()
        }
        self._issuer = issuer
        self._audience = audience
        self._agent_id = agent_id

    def verify(
        self,
        assertion: PairingAssertion | str,
        *,
        request: PairingRequest | None = None,
        at: datetime | None = None,
    ) -> PairingAssertionClaims:
        compact = (
            assertion.compact_jws
            if isinstance(assertion, PairingAssertion)
            else assertion
        )
        header = _compact_header(compact, code=ClientTrustErrorCode.INVALID_ASSERTION)
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion signer is unknown.",
            )
        if (
            set(header) != {"typ", "alg", "kid"}
            or header.get("alg") != _ALGORITHM
            or header.get("typ") != _ASSERTION_TYPE
        ):
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion type is invalid.",
            )
        token = _decode_jws(
            compact, self._keys[kid], code=ClientTrustErrorCode.INVALID_ASSERTION
        )
        if isinstance(assertion, PairingAssertion) and assertion.signer_key_id != kid:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion signer is inconsistent.",
            )
        claims = _pairing_claims(
            _as_mapping(
                token.claims,
                code=ClientTrustErrorCode.INVALID_ASSERTION,
                message="The Pairing Assertion payload is invalid.",
            )
        )
        moment = (at or datetime.now(UTC)).astimezone(UTC)
        if claims.expires_at <= moment or claims.issued_at > moment + _MAX_CLOCK_SKEW:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion is outside its validity period.",
            )
        if self._issuer is not None and claims.issuer != self._issuer:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion issuer is invalid.",
            )
        if self._audience is not None and claims.audience != self._audience:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion audience is invalid.",
            )
        if self._agent_id is not None and claims.agent_id != self._agent_id:
            raise _invalid(
                ClientTrustErrorCode.INVALID_ASSERTION,
                "The Pairing Assertion target is invalid.",
            )
        if request is not None:
            if (
                claims.pairing_request_id != request.request_id
                or claims.jwk_thumbprint != request.browser_jwk_thumbprint
            ):
                raise _invalid(
                    ClientTrustErrorCode.INVALID_ASSERTION,
                    "The Pairing Assertion does not match the request.",
                )
            if claims.expires_at > request.expires_at:
                raise _invalid(
                    ClientTrustErrorCode.INVALID_ASSERTION,
                    "The Pairing Assertion exceeds the request lifetime.",
                )
        return claims


def _compact_header(value: str, *, code: ClientTrustErrorCode) -> Mapping[str, Any]:
    if not isinstance(value, str) or value.count(".") != 2:
        raise _invalid(code, "The signed value is not a compact JWS.")
    try:
        raw = base64.urlsafe_b64decode(value.split(".", 1)[0] + "===")
        import json

        header = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise _invalid(
            code, "The signed value has an invalid protected header."
        ) from exc
    return _as_mapping(
        header, code=code, message="The signed value has an invalid protected header."
    )


def _pairing_payload(claims: PairingAssertionClaims) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "iss": claims.issuer,
        "sub": claims.subject,
        "aud": claims.audience,
        "iat": int(claims.issued_at.timestamp()),
        "exp": int(claims.expires_at.timestamp()),
        "jti": claims.jti,
        "pairing_request_id": claims.pairing_request_id,
        "agent_id": claims.agent_id,
        "jwk_thumbprint": claims.jwk_thumbprint,
        "database": claims.business.database,
        "company_id": claims.business.company_id,
        "organization_id": claims.business.organization_id,
        "site_id": claims.business.site_id,
        "actor_id": claims.actor_id,
        "role": claims.role,
        "scopes": sorted(scope.value for scope in claims.scopes),
        "session_nonce": claims.session_nonce,
    }
    if claims.business.pos_configuration_id is not None:
        payload["pos_configuration_id"] = claims.business.pos_configuration_id
    return payload


__all__ = [
    "PairingAssertionSigner",
    "PairingAssertionVerifier",
    "jwk_thumbprint",
    "public_ed25519_jwk",
]
