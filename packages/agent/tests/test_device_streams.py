from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import pytest

from inari.client_trust import (
    AcceptedDPoPProof,
    AuthorizedRequest,
    BoundOrigin,
    BusinessScope,
    ClientGrant,
    EndpointPolicy,
    PairingScope,
    Permission,
    RequestTarget,
)
from inari.core.failures import DomainFailure, ProblemCode
from inari.db.schema import metadata
from inari.device_authority import AdmissionPermit, CapabilityStreamTarget
from inari.device_streams import (
    AgentEventSigner,
    BarcodeSample,
    DeviceStreamKind,
    DeviceStreamService,
    EventLeaseRequest,
    ScaleRangeState,
    ScaleSample,
    SqliteDeviceStreamLedger,
    StreamMessageKind,
    StreamSelection,
)
from inari.runtime.store import RuntimeStore
from inari.security.identity import AgentIdentityService

from .support.device_authority import authority_proof


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


@dataclass(slots=True)
class Clock:
    value: datetime = NOW
    monotonic_value: float = 100.0

    def __call__(self) -> datetime:
        return self.value

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)
        self.monotonic_value += seconds


@dataclass(slots=True)
class MemoryLedger:
    sequence: int = 0
    generations: dict[str, int] = field(default_factory=dict)

    def current_sequence(self) -> int:
        return self.sequence

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def next_generation(self, scope_digest: str) -> int:
        value = self.generations.get(scope_digest, 0) + 1
        self.generations[scope_digest] = value
        return value


@dataclass(slots=True)
class Authority:
    checked: int = 0
    fail_checks: bool = False
    targets: list[CapabilityStreamTarget] = field(default_factory=list)

    def authorize_stream(self, target: CapabilityStreamTarget, *, now=None):
        self.targets.append(target)
        matrix_row_id = (
            "cert_scale_1" if target.operation == "scale_reading" else "cert_scanner_1"
        )
        proof = replace(
            authority_proof(),
            binding_revision_id=target.binding_revision_id,
            device_id=target.device_id,
            purpose=target.purpose,
            operation=target.operation,
            media_type=(
                "application/inari-scale-reading+json"
                if target.operation == "scale_reading"
                else "application/inari-barcode-event+json"
            ),
            matrix_row_id=matrix_row_id,
            issued_at=NOW - timedelta(minutes=1),
            valid_until=NOW + timedelta(hours=1),
        )
        return AdmissionPermit._issue(
            proof, hashlib.sha256(target.device_id.encode()).digest()
        )

    def check(self, permit: AdmissionPermit, *, now=None):
        self.checked += 1
        if self.fail_checks:
            raise RuntimeError("authority changed")
        return permit.authority_proof


@dataclass(slots=True)
class Signer:
    agent_id: str = "agent_1"
    key_id: str = "key_1"
    public_jwk: dict[str, str] = field(
        default_factory=lambda: {
            "kty": "OKP",
            "crv": "Ed25519",
            "kid": "key_1",
            "x": "public_key_value",
        }
    )

    def sign(self, document):
        digest = hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return f"signature_{digest}"


def authorization(
    *,
    pairing_id: str = "pairing_1",
    pos_configuration_id: str = "pos_1",
    permissions: frozenset[Permission] | None = None,
) -> AuthorizedRequest:
    browser_origin = BoundOrigin("https://odoo.example")
    agent_endpoint = BoundOrigin("https://agent.example")
    business = BusinessScope(
        database="odoo",
        company_id="company_1",
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id=pos_configuration_id,
    )
    scope = PairingScope(
        agent_id="agent_1",
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        audience="inari.local",
    )
    target = RequestTarget("POST", "https://agent.example/v1/events/lease")
    grant = ClientGrant(
        grant_id=f"grant_{pairing_id}",
        pairing_id=pairing_id,
        jwk_thumbprint="thumbprint_1",
        scope=scope,
        actor_id="res.users:7",
        role="device_operator",
        permissions=permissions
        or frozenset({Permission.EVENTS_READ, Permission.SCALE, Permission.SCANNER}),
        authorization_digest="authorization_digest_1",
        generation=1,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=14),
    )
    dpop = AcceptedDPoPProof(
        jwk_thumbprint=grant.jwk_thumbprint,
        htm=target.method,
        htu=target.uri,
        iat=NOW,
        ath="access_hash_1",
        nonce="nonce_value_1",
        jti=f"proof_{pairing_id}",
        accepted_at=NOW,
    )
    endpoint = EndpointPolicy(
        agent_id=scope.agent_id,
        audience=scope.audience,
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        allowed_methods=frozenset({"POST"}),
        allowed_paths=("/v1/events/lease",),
    )
    return AuthorizedRequest(
        target=target,
        grant=grant,
        dpop=dpop,
        endpoint=endpoint,
        accepted_at=NOW,
    )


def scale_selection() -> StreamSelection:
    return StreamSelection(
        kind=DeviceStreamKind.SCALE,
        device_id="scale_1",
        binding_revision_id="binding_scale_1",
        certification_id="cert_scale_1",
    )


def scanner_selection() -> StreamSelection:
    return StreamSelection(
        kind=DeviceStreamKind.SCANNER,
        device_id="scanner_1",
        binding_revision_id="binding_scanner_1",
    )


def build_service(clock: Clock | None = None):
    clock = clock or Clock()
    authority = Authority()
    service = DeviceStreamService(
        ledger=MemoryLedger(),
        authority=authority,
        signer=Signer(),
        clock=clock,
        monotonic=clock.monotonic,
        agent_boot_id="boot_test_1",
    )
    return service, authority, clock


@pytest.mark.anyio
async def test_acquire_is_scoped_idempotent_and_fenced() -> None:
    service, authority, _clock = build_service()
    auth = authorization()
    request = EventLeaseRequest(
        holder_id="holder_123456789",
        selections=(scale_selection(), scanner_selection()),
    )

    first = await service.acquire(request, auth)
    replay = await service.acquire(request, auth)

    assert replay == first
    assert first.generation == 1
    assert first.agent_boot_id == "boot_test_1"
    assert [target.operation for target in authority.targets] == [
        "scale_reading",
        "barcode_event",
        "scale_reading",
        "barcode_event",
    ]

    with pytest.raises(DomainFailure) as conflict:
        await service.acquire(
            EventLeaseRequest(
                holder_id="other_holder_1234",
                selections=request.selections,
            ),
            auth,
        )
    assert conflict.value.code is ProblemCode.TRANSPORT_LEADER_ACTIVE


@pytest.mark.anyio
async def test_acquire_requires_exact_device_read_permission() -> None:
    service, _authority, _clock = build_service()
    auth = authorization(permissions=frozenset({Permission.EVENTS_READ}))

    with pytest.raises(DomainFailure) as failure:
        await service.acquire(
            EventLeaseRequest(
                holder_id="holder_123456789",
                selections=(scale_selection(),),
            ),
            auth,
        )

    assert failure.value.code is ProblemCode.PERMISSION_DENIED


@pytest.mark.anyio
async def test_scale_lease_is_exclusive_across_pos_scopes() -> None:
    service, _authority, _clock = build_service()
    first_auth = authorization(pairing_id="pairing_1", pos_configuration_id="pos_1")
    second_auth = authorization(pairing_id="pairing_2", pos_configuration_id="pos_2")
    selection = (scale_selection(),)
    first = await service.acquire(
        EventLeaseRequest("holder_123456789", selection), first_auth
    )
    second = await service.acquire(
        EventLeaseRequest("holder_987654321", selection), second_auth
    )

    scale_lease = await service.acquire_scale_lease(
        first.lease_id, first.generation, first_auth
    )
    assert scale_lease.device_id == "scale_1"

    with pytest.raises(DomainFailure) as failure:
        await service.acquire_scale_lease(
            second.lease_id, second.generation, second_auth
        )
    assert failure.value.code is ProblemCode.SCALE_IN_USE


@pytest.mark.anyio
async def test_scale_stream_emits_exact_signed_readings_and_rate_limits() -> None:
    service, authority, clock = build_service()
    auth = authorization()
    lease = await service.acquire(
        EventLeaseRequest("holder_123456789", (scale_selection(),)), auth
    )
    scale_lease = await service.acquire_scale_lease(
        lease.lease_id, lease.generation, auth
    )
    events = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=scale_lease.scale_lease_id,
    )

    ready = await anext(events)
    assert ready.kind is StreamMessageKind.READY
    assert ready.payload["high_water_mark"] == 0

    sample = ScaleSample(
        device_id="scale_1",
        binding_revision_id="binding_scale_1",
        observed_at=clock.value,
        monotonic_ms=100_000,
        value_mantissa=12_345,
        decimal_exponent=-3,
        unit="kg",
        resolution_mantissa=1,
        stable=True,
        range_state=ScaleRangeState.VALID,
    )
    assert await service.publish_scale(sample) == 1
    assert await service.publish_scale(sample) == 0

    reading = await anext(events)
    assert reading.kind is StreamMessageKind.SCALE_READING
    assert reading.stream_sequence == 1
    assert reading.payload["value_mantissa"] == 12_345
    assert reading.payload["decimal_exponent"] == -3
    assert reading.payload["certification_id"] == "cert_scale_1"
    assert reading.payload["client_grant_id"] == auth.grant.grant_id
    assert "signature" in reading.signature
    assert authority.checked == 1

    clock.advance(0.5)
    assert await service.publish_scale(replace(sample, observed_at=clock.value)) == 1
    second = await anext(events)
    assert second.payload["sequence"] == 2
    await events.aclose()


@pytest.mark.anyio
async def test_scanner_replays_until_ack_and_never_persists_value(
    tmp_path: Path,
) -> None:
    clock = Clock()
    store = RuntimeStore(tmp_path / "agent.sqlite3")
    metadata.create_all(store.engine)
    service = DeviceStreamService(
        ledger=SqliteDeviceStreamLedger(store),
        authority=Authority(),
        signer=Signer(),
        clock=clock,
        monotonic=clock.monotonic,
        agent_boot_id="boot_test_1",
    )
    auth = authorization()
    lease = await service.acquire(
        EventLeaseRequest("holder_123456789", (scanner_selection(),)), auth
    )
    events = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=None,
    )
    await anext(events)
    value = "5901234123457"
    assert (
        await service.publish_barcode(
            BarcodeSample(
                device_id="scanner_1",
                binding_revision_id="binding_scanner_1",
                observed_at=clock.value,
                monotonic_ms=100_000,
                symbology="ean13",
                value=value,
            )
        )
        == 1
    )
    barcode = await anext(events)
    assert barcode.kind is StreamMessageKind.BARCODE
    assert barcode.payload["value"] == value
    await events.aclose()

    replay = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=None,
        last_event_id=0,
    )
    await anext(replay)
    replayed_barcode = await anext(replay)
    assert replayed_barcode.document() == barcode.document()
    sequence = barcode.payload["sequence"]
    assert isinstance(sequence, int)
    await service.acknowledge(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        {"scanner_1": sequence},
        auth,
    )
    await service.acknowledge(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        {"scanner_1": sequence},
        auth,
    )
    await replay.aclose()

    assert value.encode() not in (tmp_path / "agent.sqlite3").read_bytes()


@pytest.mark.anyio
async def test_lease_expiry_emits_signed_terminal_message() -> None:
    clock = Clock()
    service = DeviceStreamService(
        ledger=MemoryLedger(),
        authority=Authority(),
        signer=Signer(),
        clock=clock,
        monotonic=clock.monotonic,
        agent_boot_id="boot_test_1",
        event_lease_ttl=timedelta(seconds=1),
    )
    auth = authorization()
    lease = await service.acquire(
        EventLeaseRequest("holder_123456789", (scanner_selection(),)), auth
    )
    events = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=None,
    )
    await anext(events)
    clock.advance(1.1)

    terminal = await anext(events)

    assert terminal.kind is StreamMessageKind.LEASE_LOST
    assert terminal.payload["reason"] == "expired"
    with pytest.raises(StopAsyncIteration):
        await anext(events)


@pytest.mark.anyio
async def test_scale_lease_expiry_invalidates_scale_without_closing_transport() -> None:
    clock = Clock()
    service = DeviceStreamService(
        ledger=MemoryLedger(),
        authority=Authority(),
        signer=Signer(),
        clock=clock,
        monotonic=clock.monotonic,
        agent_boot_id="boot_test_1",
        scale_lease_ttl=timedelta(seconds=1),
    )
    auth = authorization()
    lease = await service.acquire(
        EventLeaseRequest("holder_123456789", (scale_selection(),)), auth
    )
    scale_lease = await service.acquire_scale_lease(
        lease.lease_id, lease.generation, auth
    )
    events = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=scale_lease.scale_lease_id,
    )
    await anext(events)
    clock.advance(1.1)

    lost = await anext(events)

    assert lost.kind is StreamMessageKind.SCALE_LEASE_LOST
    assert lost.payload == {
        "lease_id": lease.lease_id,
        "subscription_id": lease.subscription_id,
        "scope_digest": lease.scope_digest,
        "device_id": "scale_1",
        "reason": "expired",
    }
    await events.aclose()


@pytest.mark.anyio
async def test_scanner_buffer_overflow_fails_closed_without_value_evidence() -> None:
    service, _authority, clock = build_service()
    auth = authorization()
    lease = await service.acquire(
        EventLeaseRequest("holder_123456789", (scanner_selection(),)), auth
    )
    events = service.stream(
        lease.lease_id,
        lease.subscription_id,
        lease.generation,
        auth,
        scale_lease_id=None,
    )
    await anext(events)

    for sequence in range(129):
        await service.publish_barcode(
            BarcodeSample(
                device_id="scanner_1",
                binding_revision_id="binding_scanner_1",
                observed_at=clock.value,
                monotonic_ms=100_000 + sequence,
                symbology="ean13",
                value=f"private-value-{sequence}",
            )
        )

    terminal = await anext(events)

    assert terminal.kind is StreamMessageKind.REPLAY_UNAVAILABLE
    assert terminal.payload["reason"] == "buffer_overflow"
    assert "private-value" not in json.dumps(terminal.document())


def test_sqlite_ledger_persists_sequence_and_generation(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "agent.sqlite3")
    metadata.create_all(store.engine)
    first = SqliteDeviceStreamLedger(store)

    assert first.current_sequence() == 0
    assert first.next_sequence() == 1
    assert first.next_generation("scope_12345678") == 1

    reopened = SqliteDeviceStreamLedger(RuntimeStore(tmp_path / "agent.sqlite3"))
    assert reopened.current_sequence() == 1
    assert reopened.next_sequence() == 2
    assert reopened.next_generation("scope_12345678") == 2


def test_agent_event_signer_uses_verifiable_canonical_jws(tmp_path: Path) -> None:
    identity = AgentIdentityService(identity_path=tmp_path / "agent-identity.pem")
    signer = AgentEventSigner(identity)
    document = {"kind": "ready", "stream_sequence": 0, "payload": {"ok": True}}

    compact = signer.sign(document)
    protected, payload, signature = compact.split(".")
    header = json.loads(_decode_base64url(protected))
    assert header == {
        "alg": "EdDSA",
        "kid": signer.key_id,
        "typ": "inari-agent-event+jws",
    }
    assert json.loads(_decode_base64url(payload)) == document
    public_key = Ed25519PublicKey.from_public_bytes(
        _decode_base64url(signer.public_jwk["x"])
    )
    public_key.verify(
        _decode_base64url(signature),
        f"{protected}.{payload}".encode("ascii"),
    )


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
