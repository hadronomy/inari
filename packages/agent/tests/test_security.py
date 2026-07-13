from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from inari.config import AgentSettings
from inari.core.exceptions import AgentError
from inari.security.identity import AgentIdentityService
from inari.security.local_trust import (
    LocalChallengePurpose,
    LocalClientAttestation,
    LocalTrustStore,
    StandaloneTrustService,
)
from inari.security.local_trust.crypto import (
    generate_local_client_key_pair,
    sign_local_challenge,
)
from inari.security.models import AccessScope, GatewayExposure
from inari.security.policies import SecurityPolicyService
from inari.security.secrets import MemorySecretStore
from inari.security.tokens import TokenService


def test_identity_is_stable_across_reloads(tmp_path: Path) -> None:
    identity_path = tmp_path / "identity.pem"
    first_service = AgentIdentityService(identity_path=identity_path)
    second_service = AgentIdentityService(identity_path=identity_path)

    first = first_service.get_or_create_identity()
    second = second_service.get_or_create_identity()

    assert first.agent_id == second.agent_id
    assert first.key_id == second.key_id
    assert first.public_jwk == second.public_jwk


def test_token_service_round_trips_local_token(tmp_path: Path) -> None:
    identity_service = AgentIdentityService(identity_path=tmp_path / "identity.pem")
    token_service = TokenService(
        secret_store=MemorySecretStore(),
        identity_service=identity_service,
        token_ttl_seconds=3600,
        token_audience="inari.local",
    )

    token = token_service.issue_local_token(
        client_name="tray",
        scopes=(AccessScope.SYSTEM_READ, AccessScope.EVENTS_READ),
    )
    principal = token_service.authenticate_token(token.access_token)

    assert principal.subject == "local:tray"
    assert principal.has_scopes((AccessScope.SYSTEM_READ,))
    assert not principal.has_scopes((AccessScope.ADMIN_WRITE,))


def test_loopback_settings_validate_cleanly() -> None:
    settings = AgentSettings()

    SecurityPolicyService(settings).validate_startup()


def test_native_pairing_remains_available_when_http_bootstrap_is_disabled() -> None:
    service = StandaloneTrustService(
        settings=AgentSettings(allow_loopback_bootstrap=False),
        store=LocalTrustStore(MemorySecretStore()),
    )

    with pytest.raises(AgentError, match="disabled"):
        service.start_pairing()

    assert service.start_native_pairing().secret


def test_lan_settings_require_tls() -> None:
    settings = AgentSettings(host="0.0.0.0", gateway_exposure=GatewayExposure.LAN)

    with pytest.raises(RuntimeError):
        SecurityPolicyService(settings).validate_startup()


def test_standalone_trust_pairs_and_authorizes_signed_client() -> None:
    service = StandaloneTrustService(
        settings=AgentSettings(),
        store=LocalTrustStore(MemorySecretStore()),
    )
    key_pair = generate_local_client_key_pair(prefix="tray")
    pairing = service.start_pairing()
    pairing_challenge = service.issue_challenge(
        purpose=LocalChallengePurpose.PAIRING,
        client_id=key_pair.client_id,
    )

    client = service.complete_pairing(
        client_id=key_pair.client_id,
        client_name="inari-tray",
        public_key_pem=key_pair.public_key_pem,
        pairing_secret=pairing.secret,
        attestation=LocalClientAttestation(
            client_id=key_pair.client_id,
            challenge_id=pairing_challenge.id,
            signature=sign_local_challenge(
                private_key_pem=key_pair.private_key_pem,
                purpose=LocalChallengePurpose.PAIRING,
                challenge=pairing_challenge.challenge,
            ),
        ),
        origin=None,
    )
    token_challenge = service.issue_challenge(
        purpose=LocalChallengePurpose.TOKEN,
        client_id=key_pair.client_id,
    )

    grant = service.authorize_token_request(
        client_name="inari-tray",
        attestation=LocalClientAttestation(
            client_id=client.client_id,
            challenge_id=token_challenge.id,
            signature=sign_local_challenge(
                private_key_pem=key_pair.private_key_pem,
                purpose=LocalChallengePurpose.TOKEN,
                challenge=token_challenge.challenge,
            ),
        ),
        origin=None,
    )

    assert grant.client_id == key_pair.client_id
    assert grant.token_claims()["trust_level"] == "paired_native"


def test_standalone_trust_rejects_unpaired_client_when_pairing_is_required() -> None:
    service = StandaloneTrustService(
        settings=AgentSettings(),
        store=LocalTrustStore(MemorySecretStore()),
    )

    with pytest.raises(AgentError) as exc_info:
        service.authorize_token_request(
            client_name="unknown",
            attestation=None,
            origin=None,
        )

    assert exc_info.value.code == "LOCAL_PAIRING_REQUIRED"


def test_standalone_trust_rejects_replayed_token_challenge() -> None:
    paired = _paired_tray_service()
    challenge = paired.service.issue_challenge(
        purpose=LocalChallengePurpose.TOKEN,
        client_id=paired.client_id,
    )
    attestation = paired.attestation(
        purpose=LocalChallengePurpose.TOKEN,
        challenge_id=challenge.id,
        challenge=challenge.challenge,
    )

    paired.service.authorize_token_request(
        client_name="inari-tray",
        attestation=attestation,
        origin=None,
    )

    with pytest.raises(AgentError) as exc_info:
        paired.service.authorize_token_request(
            client_name="inari-tray",
            attestation=attestation,
            origin=None,
        )

    assert exc_info.value.code == "LOCAL_CHALLENGE_INVALID"


def test_standalone_trust_consumes_failed_attestation_challenge() -> None:
    paired = _paired_tray_service()
    attacker_key_pair = generate_local_client_key_pair(prefix="tray")
    challenge = paired.service.issue_challenge(
        purpose=LocalChallengePurpose.TOKEN,
        client_id=paired.client_id,
    )
    invalid_attestation = LocalClientAttestation(
        client_id=paired.client_id,
        challenge_id=challenge.id,
        signature=sign_local_challenge(
            private_key_pem=attacker_key_pair.private_key_pem,
            purpose=LocalChallengePurpose.TOKEN,
            challenge=challenge.challenge,
        ),
    )

    with pytest.raises(AgentError) as first_error:
        paired.service.authorize_token_request(
            client_name="inari-tray",
            attestation=invalid_attestation,
            origin=None,
        )

    with pytest.raises(AgentError) as replay_error:
        paired.service.authorize_token_request(
            client_name="inari-tray",
            attestation=paired.attestation(
                purpose=LocalChallengePurpose.TOKEN,
                challenge_id=challenge.id,
                challenge=challenge.challenge,
            ),
            origin=None,
        )

    assert first_error.value.code == "LOCAL_CLIENT_ATTESTATION_FAILED"
    assert replay_error.value.code == "LOCAL_CHALLENGE_INVALID"


def test_standalone_trust_expires_pairing_secret() -> None:
    clock = FrozenClock(datetime(2026, 4, 21, tzinfo=UTC))
    service = StandaloneTrustService(
        settings=AgentSettings(local_pairing_secret_ttl_seconds=1),
        store=LocalTrustStore(MemorySecretStore()),
        clock=clock,
    )
    key_pair = generate_local_client_key_pair(prefix="tray")
    pairing = service.start_pairing()
    challenge = service.issue_challenge(
        purpose=LocalChallengePurpose.PAIRING,
        client_id=key_pair.client_id,
    )

    clock.advance(seconds=2)

    with pytest.raises(AgentError) as exc_info:
        service.complete_pairing(
            client_id=key_pair.client_id,
            client_name="inari-tray",
            public_key_pem=key_pair.public_key_pem,
            pairing_secret=pairing.secret,
            attestation=LocalClientAttestation(
                client_id=key_pair.client_id,
                challenge_id=challenge.id,
                signature=sign_local_challenge(
                    private_key_pem=key_pair.private_key_pem,
                    purpose=LocalChallengePurpose.PAIRING,
                    challenge=challenge.challenge,
                ),
            ),
            origin=None,
        )

    assert exc_info.value.code == "LOCAL_PAIRING_NOT_STARTED"
    assert service.current_state().pairing_secret is None


def test_standalone_trust_keeps_native_clients_usable_without_origin() -> None:
    paired = _paired_tray_service()
    challenge = paired.service.issue_challenge(
        purpose=LocalChallengePurpose.TOKEN,
        client_id=paired.client_id,
    )

    grant = paired.service.authorize_token_request(
        client_name="inari-tray",
        attestation=paired.attestation(
            purpose=LocalChallengePurpose.TOKEN,
            challenge_id=challenge.id,
            challenge=challenge.challenge,
        ),
        origin=None,
    )

    assert grant.client_id == paired.client_id
    assert grant.origin is None


def test_standalone_trust_rejects_unpaired_browser_origin() -> None:
    paired = _paired_tray_service(
        settings=AgentSettings(
            local_trusted_origins=[
                "http://paired.example",
                "http://other.example",
            ]
        ),
        origin="http://paired.example",
    )
    challenge = paired.service.issue_challenge(
        purpose=LocalChallengePurpose.TOKEN,
        client_id=paired.client_id,
    )

    with pytest.raises(AgentError) as exc_info:
        paired.service.authorize_token_request(
            client_name="browser",
            attestation=paired.attestation(
                purpose=LocalChallengePurpose.TOKEN,
                challenge_id=challenge.id,
                challenge=challenge.challenge,
                origin="http://other.example",
            ),
            origin="http://other.example",
        )

    assert exc_info.value.code == "LOCAL_ORIGIN_NOT_PAIRED"


def test_standalone_trust_rejects_pairing_start_after_pairing_completed() -> None:
    paired = _paired_tray_service()

    with pytest.raises(AgentError) as exc_info:
        paired.service.start_pairing()

    assert exc_info.value.code == "LOCAL_PAIRING_ALREADY_COMPLETED"


@dataclass(slots=True)
class PairedTrayService:
    service: StandaloneTrustService
    private_key_pem: str
    client_id: str

    def attestation(
        self,
        *,
        purpose: LocalChallengePurpose,
        challenge_id: str,
        challenge: str,
        origin: str | None = None,
    ) -> LocalClientAttestation:
        return LocalClientAttestation(
            client_id=self.client_id,
            challenge_id=challenge_id,
            signature=sign_local_challenge(
                private_key_pem=self.private_key_pem,
                purpose=purpose,
                challenge=challenge,
            ),
            origin=origin,
        )


class FrozenClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


def _paired_tray_service(
    *,
    settings: AgentSettings | None = None,
    origin: str | None = None,
) -> PairedTrayService:
    service = StandaloneTrustService(
        settings=settings or AgentSettings(),
        store=LocalTrustStore(MemorySecretStore()),
    )
    key_pair = generate_local_client_key_pair(prefix="tray")
    pairing = service.start_pairing()
    challenge = service.issue_challenge(
        purpose=LocalChallengePurpose.PAIRING,
        client_id=key_pair.client_id,
    )
    service.complete_pairing(
        client_id=key_pair.client_id,
        client_name="inari-tray",
        public_key_pem=key_pair.public_key_pem,
        pairing_secret=pairing.secret,
        attestation=LocalClientAttestation(
            client_id=key_pair.client_id,
            challenge_id=challenge.id,
            signature=sign_local_challenge(
                private_key_pem=key_pair.private_key_pem,
                purpose=LocalChallengePurpose.PAIRING,
                challenge=challenge.challenge,
            ),
        ),
        origin=origin,
    )
    return PairedTrayService(
        service=service,
        private_key_pem=key_pair.private_key_pem,
        client_id=key_pair.client_id,
    )
