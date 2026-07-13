from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from ...config import AgentSettings
from ...core.exceptions import AgentError
from ..models import GatewayMode
from .crypto import (
    public_key_fingerprint,
    verify_local_challenge_signature,
)
from .models import (
    LocalChallenge,
    LocalChallengePurpose,
    LocalClientAttestation,
    LocalPairingSecret,
    LocalTrustGrant,
    LocalTrustLevel,
    LocalTrustState,
    TrustedLocalClient,
)
from .store import LocalTrustStore


@dataclass(slots=True, frozen=True)
class PairingStartResult:
    secret: str
    expires_at: datetime


class LocalChallengeRegistry:
    def __init__(self, *, clock: Callable[[], datetime], ttl_seconds: int) -> None:
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._challenges: dict[str, LocalChallenge] = {}
        self._lock = threading.Lock()

    def issue(
        self,
        *,
        purpose: LocalChallengePurpose,
        client_id: str | None = None,
    ) -> LocalChallenge:
        now = self._clock()
        challenge = LocalChallenge(
            id=token_urlsafe(18),
            challenge=token_urlsafe(32),
            purpose=purpose,
            client_id=client_id,
            expires_at=now + timedelta(seconds=self._ttl_seconds),
        )
        with self._lock:
            self._discard_expired(now)
            self._challenges[challenge.id] = challenge
        return challenge

    def consume(
        self,
        *,
        challenge_id: str,
        purpose: LocalChallengePurpose,
        client_id: str,
    ) -> LocalChallenge:
        now = self._clock()
        with self._lock:
            self._discard_expired(now)
            challenge = self._challenges.pop(challenge_id, None)
        if challenge is None:
            raise AgentError(
                "LOCAL_CHALLENGE_INVALID",
                "The local trust challenge is invalid or expired.",
                status_code=403,
            )
        if challenge.purpose is not purpose:
            raise AgentError(
                "LOCAL_CHALLENGE_INVALID",
                "The local trust challenge was issued for a different purpose.",
                status_code=403,
            )
        if challenge.client_id is not None and challenge.client_id != client_id:
            raise AgentError(
                "LOCAL_CHALLENGE_INVALID",
                "The local trust challenge was issued for a different client.",
                status_code=403,
            )
        return challenge

    def _discard_expired(self, now: datetime) -> None:
        expired = [
            challenge_id
            for challenge_id, challenge in self._challenges.items()
            if challenge.expires_at <= now
        ]
        for challenge_id in expired:
            del self._challenges[challenge_id]


class StandaloneTrustService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        store: LocalTrustStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._challenge_registry = LocalChallengeRegistry(
            clock=self._now,
            ttl_seconds=settings.local_pairing_secret_ttl_seconds,
        )
        self._state_lock = threading.Lock()
        if settings.standalone_pairing_secret:
            self.ensure_pairing_secret(settings.standalone_pairing_secret)

    @property
    def pairing_required(self) -> bool:
        return (
            self.settings.gateway_mode is GatewayMode.STANDALONE
            and self.settings.local_pairing_required
        )

    def current_state(self) -> LocalTrustState:
        with self._state_lock:
            return self._current_state_locked()

    def _current_state_locked(self) -> LocalTrustState:
        state = self.store.load()
        pairing_secret = state.pairing_secret
        if pairing_secret is not None and pairing_secret.is_expired(self._now()):
            state = self.store.clear_pairing_secret()
        return state

    def issue_challenge(
        self,
        *,
        purpose: LocalChallengePurpose,
        client_id: str | None = None,
    ) -> LocalChallenge:
        return self._challenge_registry.issue(purpose=purpose, client_id=client_id)

    def start_pairing(self, *, allow_when_paired: bool = False) -> PairingStartResult:
        self._assert_standalone()
        if not self.settings.allow_loopback_bootstrap:
            raise AgentError(
                "LOCAL_PAIRING_BOOTSTRAP_DISABLED",
                "Local pairing bootstrap is disabled by configuration.",
                status_code=403,
            )
        return self._start_pairing(allow_when_paired=allow_when_paired)

    def start_native_pairing(self) -> PairingStartResult:
        """Issue bootstrap material after a native transport authenticates its peer."""

        self._assert_standalone()
        return self._start_pairing(allow_when_paired=False)

    def _start_pairing(self, *, allow_when_paired: bool) -> PairingStartResult:
        with self._state_lock:
            if self._current_state_locked().paired and not allow_when_paired:
                raise AgentError(
                    "LOCAL_PAIRING_ALREADY_COMPLETED",
                    "Local pairing has already been completed. Rotate pairing from an authenticated admin client.",
                    status_code=409,
                )
            secret = token_urlsafe(32)
            pairing_secret = self._build_pairing_secret(secret)
            self.store.set_pairing_secret(pairing_secret)
        return PairingStartResult(secret=secret, expires_at=pairing_secret.expires_at)

    def ensure_pairing_secret(self, secret: str) -> LocalPairingSecret:
        with self._state_lock:
            pairing_secret = self._build_pairing_secret(secret)
            self.store.set_pairing_secret(pairing_secret)
        return pairing_secret

    def _build_pairing_secret(self, secret: str) -> LocalPairingSecret:
        now = self._now()
        return LocalPairingSecret(
            secret_hash=_hash_secret(secret),
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.settings.local_pairing_secret_ttl_seconds),
        )

    def complete_pairing(
        self,
        *,
        client_id: str,
        client_name: str,
        public_key_pem: str,
        pairing_secret: str,
        attestation: LocalClientAttestation,
        origin: str | None,
    ) -> TrustedLocalClient:
        self._assert_standalone()
        pairing_secret_hash = _hash_secret(pairing_secret)
        with self._state_lock:
            stored_secret = self._current_state_locked().pairing_secret
            if stored_secret is None:
                raise AgentError(
                    "LOCAL_PAIRING_NOT_STARTED",
                    "Start local pairing before completing it.",
                    status_code=409,
                )
            if not hmac.compare_digest(
                stored_secret.secret_hash,
                pairing_secret_hash,
            ):
                raise AgentError(
                    "LOCAL_PAIRING_SECRET_INVALID",
                    "The local pairing secret is invalid.",
                    status_code=403,
                )
        self._verify_attestation(
            attestation=attestation,
            public_key_pem=public_key_pem,
            purpose=LocalChallengePurpose.PAIRING,
            expected_client_id=client_id,
        )
        fingerprint = public_key_fingerprint(public_key_pem)
        if client_id != f"tray_{fingerprint[:24]}" and not client_id.startswith(
            "local_"
        ):
            raise AgentError(
                "LOCAL_CLIENT_ID_INVALID",
                "The local client id does not match the presented public key.",
                status_code=400,
            )
        now = self._now()
        trust_level = (
            LocalTrustLevel.PAIRED_BROWSER
            if origin is not None
            else LocalTrustLevel.PAIRED_NATIVE
        )
        client = TrustedLocalClient(
            client_id=client_id,
            client_name=client_name,
            public_key_pem=public_key_pem,
            trust_level=trust_level,
            origins=[origin] if origin else [],
            paired_at=now,
            last_seen_at=now,
        )
        with self._state_lock:
            stored_secret = self._current_state_locked().pairing_secret
            if stored_secret is None or not hmac.compare_digest(
                stored_secret.secret_hash,
                pairing_secret_hash,
            ):
                raise AgentError(
                    "LOCAL_PAIRING_NOT_STARTED",
                    "The local pairing secret expired or was rotated before pairing completed.",
                    status_code=409,
                )
            self.store.upsert_client(client)
        return client

    def authorize_token_request(
        self,
        *,
        client_name: str,
        attestation: LocalClientAttestation | None,
        origin: str | None,
    ) -> LocalTrustGrant:
        if not self.pairing_required:
            return LocalTrustGrant(
                client_id=attestation.client_id if attestation is not None else None,
                client_name=client_name,
                trust_level=LocalTrustLevel.LOOPBACK,
                origin=origin,
            )
        if attestation is None:
            raise AgentError(
                "LOCAL_PAIRING_REQUIRED",
                "This standalone agent requires local client pairing before issuing tokens.",
                status_code=403,
            )
        with self._state_lock:
            client = self._current_state_locked().client(attestation.client_id)
        if client is None:
            raise AgentError(
                "LOCAL_CLIENT_NOT_PAIRED",
                "The local client is not paired with this agent.",
                status_code=403,
            )
        self._assert_origin_allowed(client, origin or attestation.origin)
        self._verify_attestation(
            attestation=attestation,
            public_key_pem=client.public_key_pem,
            purpose=LocalChallengePurpose.TOKEN,
            expected_client_id=client.client_id,
        )
        touched = client.touched(self._now())
        with self._state_lock:
            if self._current_state_locked().client(touched.client_id) is None:
                raise AgentError(
                    "LOCAL_CLIENT_NOT_PAIRED",
                    "The local client is no longer paired with this agent.",
                    status_code=403,
                )
            self.store.touch_client(touched)
        return LocalTrustGrant(
            client_id=touched.client_id,
            client_name=touched.client_name,
            trust_level=touched.trust_level,
            origin=origin or attestation.origin,
        )

    def revoke_client(self, client_id: str) -> LocalTrustState:
        with self._state_lock:
            return self.store.revoke_client(client_id)

    def _verify_attestation(
        self,
        *,
        attestation: LocalClientAttestation,
        public_key_pem: str,
        purpose: LocalChallengePurpose,
        expected_client_id: str,
    ) -> None:
        if attestation.client_id != expected_client_id:
            raise AgentError(
                "LOCAL_CLIENT_ATTESTATION_FAILED",
                "The local client attestation was issued for a different client.",
                status_code=403,
            )
        challenge = self._challenge_registry.consume(
            challenge_id=attestation.challenge_id,
            purpose=purpose,
            client_id=attestation.client_id,
        )
        verify_local_challenge_signature(
            public_key_pem=public_key_pem,
            purpose=purpose,
            challenge=challenge.challenge,
            signature=attestation.signature,
        )

    def _assert_origin_allowed(
        self, client: TrustedLocalClient, origin: str | None
    ) -> None:
        if not self.settings.local_origin_bound_tokens or origin is None:
            return
        trusted_origins = set(self.settings.local_trusted_origins)
        if trusted_origins and origin not in trusted_origins:
            raise AgentError(
                "LOCAL_ORIGIN_NOT_TRUSTED",
                "The local client origin is not trusted.",
                status_code=403,
            )
        if not client.allows_origin(origin):
            raise AgentError(
                "LOCAL_ORIGIN_NOT_PAIRED",
                "The local client origin is not paired with this agent.",
                status_code=403,
            )

    def _assert_standalone(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.STANDALONE:
            raise AgentError(
                "LOCAL_TRUST_STANDALONE_ONLY",
                "Local pairing is only available in standalone mode.",
                status_code=400,
            )

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()
