"""Framework-free orchestration for browser Client Trust."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import secrets
from uuid import uuid4

from .errors import (
    ClientTrustError,
    ClientTrustErrorCode,
    DPoPNonceRequiredError,
    ReplayDetectedError,
    ScopeMismatchError,
)
from .models import (
    AccessTokenClaims,
    AuthorizedRequest,
    BoundOrigin,
    ClientGrant,
    ClientPairing,
    EndpointBinding,
    GrantAdmissionProof,
    GrantLifecycle,
    IssuedDPoPNonce,
    PairingCommand,
    PairingLifecycle,
    PairingRequest,
    PairingRequestState,
    PairingResult,
    PairingScope,
    RenewalCommand,
    RenewalResult,
    RequestTarget,
)
from .permissions import Permission, PermissionCatalog, PermissionSet
from .ports import (
    AccessTokenIssuerPort,
    AccessTokenVerifierPort,
    ClientTrustStore,
    DPoPNonceConsumption,
    DPoPVerifierPort,
    PairingAssertionVerifierPort,
    PermissionPolicy,
    RenewalDPoPVerifierPort,
    TrustClock,
)


_PAIRING_TTL = timedelta(minutes=10)
_GRANT_TTL = timedelta(minutes=15)
_OFFLINE_RENEWAL_TTL = timedelta(days=7)
_DPOP_NONCE_TTL = timedelta(minutes=2)
_PHRASE_WORDS = (
    "amber",
    "cedar",
    "harbor",
    "meadow",
    "orchid",
    "river",
    "silver",
    "summit",
    "willow",
)


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _digest(
    scope: PairingScope, actor_id: str, role: str, permissions: PermissionSet
) -> str:
    values = (
        scope.agent_id,
        scope.browser_origin.value,
        scope.agent_endpoint.value,
        scope.business.database,
        scope.business.company_id,
        scope.business.organization_id,
        scope.business.site_id,
        scope.business.pos_configuration_id or "",
        scope.audience,
        actor_id,
        role,
        *(
            permission.value
            for permission in sorted(permissions, key=lambda item: item.value)
        ),
    )
    return sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _invalid(message: str) -> ClientTrustError:
    return ClientTrustError(ClientTrustErrorCode.INVALID_VALUE, message)


class ClientTrustService:
    """Issue and enforce content-free browser Client Trust records."""

    def __init__(
        self,
        *,
        store: ClientTrustStore,
        assertion_verifier: PairingAssertionVerifierPort,
        access_token_verifier: AccessTokenVerifierPort,
        dpop_verifier: DPoPVerifierPort,
        renewal_dpop_verifier: RenewalDPoPVerifierPort,
        access_token_issuer: AccessTokenIssuerPort,
        clock: TrustClock,
        permission_policy: PermissionPolicy | None = None,
        pairing_ttl: timedelta = _PAIRING_TTL,
        grant_ttl: timedelta = _GRANT_TTL,
        offline_renewal_ttl: timedelta = _OFFLINE_RENEWAL_TTL,
        dpop_nonce_ttl: timedelta = _DPOP_NONCE_TTL,
        id_factory: Callable[[str], str] | None = None,
        phrase_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.assertion_verifier = assertion_verifier
        self.access_token_verifier = access_token_verifier
        self.dpop_verifier = dpop_verifier
        self.renewal_dpop_verifier = renewal_dpop_verifier
        self.access_token_issuer = access_token_issuer
        self.clock = clock
        self.permission_policy = permission_policy
        self.pairing_ttl = pairing_ttl
        self.grant_ttl = grant_ttl
        self.offline_renewal_ttl = offline_renewal_ttl
        self.dpop_nonce_ttl = dpop_nonce_ttl
        self._id_factory = id_factory or _identifier
        self._phrase_factory = phrase_factory or self._new_phrase

    @staticmethod
    def _new_phrase() -> str:
        return "-".join(secrets.choice(_PHRASE_WORDS) for _ in range(3))

    def create_pairing_request(
        self,
        *,
        scope: PairingScope,
        browser_jwk_thumbprint: str,
        requested_permissions: Iterable[Permission | str] | Permission | str,
        phrase: str | None = None,
    ) -> PairingRequest:
        """Create a ten-minute Pairing Request for one browser key."""

        values = (
            (requested_permissions,)
            if isinstance(requested_permissions, (Permission, str))
            else requested_permissions
        )
        permissions = PermissionCatalog.normalize(values)
        if not permissions:
            raise _invalid("A Pairing Request must include at least one permission.")
        now = self._now()
        request = PairingRequest(
            request_id=self._id_factory("pairing_request"),
            scope=scope,
            browser_jwk_thumbprint=browser_jwk_thumbprint,
            requested_permissions=permissions,
            session_nonce=secrets.token_urlsafe(24),
            phrase=phrase or self._phrase_factory(),
            created_at=now,
            expires_at=now + self.pairing_ttl,
        )
        self.store.save_pairing_request(request)
        return request

    def get_pairing_request(self, request_id: str) -> PairingRequest | None:
        request = self.store.get_pairing_request(request_id)
        if request is None:
            return None
        if (
            request.expires_at <= self._now()
            and request.state is PairingRequestState.PENDING
        ):
            return replace(request, state=PairingRequestState.EXPIRED)
        return request

    def admit_pairing(self, command: PairingCommand) -> PairingResult:
        """Verify one company assertion and issue one Client Pairing and Grant."""

        if not isinstance(command, PairingCommand):
            raise _invalid("The Pairing command is invalid.")
        request = self._stored_request(command.request.request_id)
        now = self._now()
        if request.state is not PairingRequestState.PENDING:
            if request.state is PairingRequestState.COMPLETED:
                raise ReplayDetectedError("The Pairing Request was already completed.")
            raise _invalid("The Pairing Request is not pending.")
        if request.expires_at <= now:
            raise ClientTrustError(
                ClientTrustErrorCode.PAIRING_EXPIRED,
                "The Pairing Request is expired.",
            )

        verified = self.assertion_verifier.verify(
            command.assertion, request=request, at=now
        )
        PairingCommand(
            request=request,
            assertion=replace(command.assertion, claims=verified),
        )
        if verified.expires_at > request.expires_at:
            raise ScopeMismatchError("The Pairing Assertion outlives the request.")

        pairing_id = self._id_factory("pairing")
        pairing = ClientPairing(
            pairing_id=pairing_id,
            pairing_request_id=request.request_id,
            jwk_thumbprint=verified.jwk_thumbprint,
            scope=request.scope,
            actor_id=verified.actor_id,
            role=verified.role,
            permissions=verified.scopes,
            created_at=now,
            expires_at=None,
        )
        grant = ClientGrant(
            grant_id=self._id_factory("grant"),
            pairing_id=pairing_id,
            jwk_thumbprint=verified.jwk_thumbprint,
            scope=request.scope,
            actor_id=verified.actor_id,
            role=verified.role,
            permissions=verified.scopes,
            authorization_digest=_digest(
                request.scope, verified.actor_id, verified.role, verified.scopes
            ),
            generation=0,
            issued_at=now,
            expires_at=now + self.grant_ttl,
            offline_renewal_until=now + self.offline_renewal_ttl,
        )
        completed_request = replace(request, state=PairingRequestState.COMPLETED)
        if not self.store.complete_pairing(
            request=completed_request,
            pairing=pairing,
            grant=grant,
            assertion_jti=verified.jti,
            at=now,
        ):
            raise ReplayDetectedError("The Pairing Assertion was already accepted.")

        admission = GrantAdmissionProof(
            pairing_request_id=request.request_id,
            assertion_jti=verified.jti,
            assertion_digest=sha256(command.assertion.compact_jws.encode()).hexdigest(),
            jwk_thumbprint=verified.jwk_thumbprint,
            admitted_at=now,
        )
        return PairingResult(pairing=pairing, grant=grant, admission=admission)

    def issue_access_token(
        self, grant: ClientGrant | str
    ) -> tuple[str, AccessTokenClaims]:
        """Issue a short access token for an active Client Grant."""

        current = self._grant(grant)
        now = self._now()
        current.active_at(now)
        return self.access_token_issuer.issue(
            current,
            issued_at=now,
            expires_at=min(current.expires_at, now + self.grant_ttl),
        )

    def issue_dpop_nonce(self) -> IssuedDPoPNonce:
        """Issue one durable, single-use DPoP nonce."""

        issued_at = self._now()
        value = IssuedDPoPNonce(
            nonce=secrets.token_urlsafe(24),
            issued_at=issued_at,
            expires_at=issued_at + self.dpop_nonce_ttl,
        )
        self.store.save_dpop_nonce(
            value.nonce,
            issued_at=value.issued_at,
            expires_at=value.expires_at,
        )
        return value

    def authorize_request(
        self,
        request: object,
        *,
        binding: EndpointBinding,
        permission: Permission | str | None = None,
    ) -> AuthorizedRequest:
        """Authorize one exact-origin request with an access token and DPoP proof."""

        target, origin, authorization, proof = self._request_values(request)
        binding.accepts(target)
        token = self._access_token(authorization)
        now = self._now()
        claims = self.access_token_verifier.verify(token, at=now)
        grant = self.store.get_grant(claims.client_grant_id)
        if grant is None:
            raise _invalid("The Client Grant is not available.")
        pairing = self.store.get_pairing(grant.pairing_id)
        if pairing is None:
            raise _invalid("The Client Pairing is not available.")
        grant.active_at(now)
        pairing.active_at(now)
        if (
            claims.issuer != binding.agent_id
            or claims.client_pairing_id != grant.pairing_id
            or claims.client_grant_id != grant.grant_id
            or claims.subject != grant.actor_id
            or claims.audience != grant.scope.audience
            or claims.cnf_jkt != grant.jwk_thumbprint
            or claims.business != grant.scope.business
            or claims.permissions != grant.permissions
            or claims.generation != grant.generation
            or claims.authorization_digest != grant.authorization_digest
        ):
            raise ScopeMismatchError(
                "The access credential does not match the Client Grant."
            )
        if (
            grant.scope != pairing.scope
            or binding.agent_id != pairing.scope.agent_id
            or binding.audience != pairing.scope.audience
            or binding.agent_endpoint != pairing.scope.agent_endpoint
        ):
            raise ScopeMismatchError(
                "The Client Grant does not match the Agent Endpoint."
            )
        if not pairing.scope.browser_origin.matches(origin):
            raise ScopeMismatchError(
                "The request origin does not match the Client Pairing."
            )
        endpoint = binding.complete(grant)
        accepted = self.dpop_verifier.verify(
            proof,
            target=target,
            claims=claims,
            access_token=token,
            at=now,
        )
        self._consume_dpop_nonce(
            accepted.nonce,
            jti=accepted.jti,
            at=now,
            replay_expires_at=claims.expires_at,
        )
        authorized = AuthorizedRequest(
            target=target,
            grant=grant,
            dpop=accepted,
            endpoint=endpoint,
            accepted_at=now,
        )
        if permission is not None:
            if self.permission_policy is not None:
                self.permission_policy.require(grant, permission)
            authorized.require(permission)
        self.store.save_grant(replace(grant, last_used_at=now))
        self.store.save_pairing(replace(pairing, last_used_at=now))
        return authorized

    def renew_grant(self, command: RenewalCommand) -> RenewalResult:
        """Renew a Client Grant only before its offline renewal deadline."""

        if not isinstance(command, RenewalCommand):
            raise _invalid("The renewal command is invalid.")
        now = self._now()
        grant = self.store.get_grant(command.grant_id)
        if grant is None:
            raise _invalid("The Client Grant is not available.")
        pairing = self.store.get_pairing(command.pairing_id)
        if pairing is None:
            raise _invalid("The Client Pairing is not available.")
        request = self.store.get_pairing_request(pairing.pairing_request_id)
        if request is None or request.state is not PairingRequestState.COMPLETED:
            raise _invalid("The Pairing Request is not available.")
        proof = self.renewal_dpop_verifier.verify(
            command.dpop,
            target=command.target,
            jwk_thumbprint=pairing.jwk_thumbprint,
            at=now,
        )
        self._consume_dpop_nonce(
            proof.nonce,
            jti=proof.jti,
            at=now,
            replay_expires_at=now + self.dpop_nonce_ttl,
        )
        pairing.active_at(now)
        if grant.lifecycle is GrantLifecycle.REVOKED:
            raise ClientTrustError(
                ClientTrustErrorCode.GRANT_REVOKED,
                "The Client Grant is revoked.",
            )
        if grant.lifecycle is GrantLifecycle.EXPIRED:
            raise ClientTrustError(
                ClientTrustErrorCode.GRANT_EXPIRED,
                "The Client Grant is expired.",
            )
        if grant.offline_renewal_until is None or grant.offline_renewal_until <= now:
            raise ClientTrustError(
                ClientTrustErrorCode.GRANT_EXPIRED,
                "The offline Client Grant renewal window is closed.",
            )
        expires_at = min(now + self.grant_ttl, grant.offline_renewal_until)
        renewed = replace(
            grant,
            generation=grant.generation + 1,
            issued_at=now,
            expires_at=expires_at,
            last_used_at=now,
        )
        _, claims = self.access_token_issuer.issue(
            renewed, issued_at=now, expires_at=expires_at
        )
        self.store.save_grant(renewed)
        return RenewalResult(grant=renewed, claims=claims)

    def revoke_pairing(self, pairing_id: str) -> ClientPairing:
        pairing = self.store.get_pairing(pairing_id)
        if pairing is None:
            raise _invalid("The Client Pairing is not available.")
        revoked = replace(pairing, lifecycle=PairingLifecycle.REVOKED)
        self.store.save_pairing(revoked)
        return revoked

    def revoke_grant(self, grant_id: str) -> ClientGrant:
        grant = self.store.get_grant(grant_id)
        if grant is None:
            raise _invalid("The Client Grant is not available.")
        revoked = replace(grant, lifecycle=GrantLifecycle.REVOKED)
        self.store.save_grant(revoked)
        return revoked

    def _now(self) -> datetime:
        value = self.clock.now()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise _invalid("The trust clock returned an invalid time.")
        return value.astimezone(UTC)

    def _consume_dpop_nonce(
        self,
        nonce: str,
        *,
        jti: str,
        at: datetime,
        replay_expires_at: datetime,
    ) -> None:
        outcome = self.store.consume_dpop_nonce(
            nonce,
            jti=jti,
            at=at,
            replay_expires_at=replay_expires_at,
        )
        if outcome is DPoPNonceConsumption.ACCEPTED:
            return
        if outcome is DPoPNonceConsumption.REPLAY:
            raise ReplayDetectedError()
        if outcome is DPoPNonceConsumption.INVALID:
            raise DPoPNonceRequiredError(self.issue_dpop_nonce())
        raise _invalid("The DPoP nonce store returned an invalid result.")

    def _stored_request(self, request_id: str) -> PairingRequest:
        request = self.store.get_pairing_request(request_id)
        if request is None:
            raise _invalid("The Pairing Request is not available.")
        return request

    def _grant(self, grant: ClientGrant | str) -> ClientGrant:
        current = (
            grant if isinstance(grant, ClientGrant) else self.store.get_grant(grant)
        )
        if current is None:
            raise _invalid("The Client Grant is not available.")
        return current

    @staticmethod
    def _access_token(value: str) -> str:
        if not isinstance(value, str):
            raise _invalid("The access credential is invalid.")
        scheme, separator, token = value.partition(" ")
        if (
            scheme != "DPoP"
            or not separator
            or not token
            or any(char.isspace() for char in token)
        ):
            raise _invalid("The access credential must use the DPoP scheme.")
        return token

    @staticmethod
    def _request_values(request: object) -> tuple[RequestTarget, str, str, str]:
        target_value = getattr(request, "target", request)
        if isinstance(target_value, RequestTarget):
            target = target_value
        else:
            try:
                target = RequestTarget(
                    getattr(target_value, "method"), getattr(target_value, "htu")
                )
            except (AttributeError, TypeError, ValueError) as exc:
                raise _invalid("The request target is invalid.") from exc
        try:
            origin = getattr(request, "origin")
            authorization = getattr(request, "authorization")
            proof = getattr(request, "dpop")
        except AttributeError as exc:
            raise _invalid("The browser request is incomplete.") from exc
        if not isinstance(origin, str):
            raise _invalid("The browser origin is invalid.")
        # Parse and canonicalize before comparison. BoundOrigin rejects null and wildcards.
        origin = BoundOrigin(origin).value
        if not isinstance(authorization, str) or not isinstance(proof, str):
            raise _invalid("The browser request credentials are invalid.")
        return target, origin, authorization, proof


__all__ = ["ClientTrustService"]
