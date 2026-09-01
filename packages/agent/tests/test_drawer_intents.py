from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from inari.core.exceptions import AgentError
from inari.core.failures import DomainFailure, ProblemCode
from inari.db.schema import metadata
from inari.drawer_intents import (
    DrawerIntentRequest,
    DrawerIntentService,
    DrawerIntentState,
    DrawerReason,
)
from inari.drawer_intents.sqlite import SqliteDrawerIntentLedger
from inari.drivers import DeviceIdentity, DeviceTransport
from inari.printing.protocols import (
    PrintJobResult,
    PrinterCapabilities,
    PrinterDevice,
    PrinterTransport,
)
from inari.runtime.store import RuntimeStore

from .support.device_authority import StaticAdmissionAuthority


NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


@dataclass(slots=True)
class Clock:
    value: datetime = NOW

    def __call__(self) -> datetime:
        return self.value


@dataclass(slots=True)
class Authority(StaticAdmissionAuthority):
    checks: int = 0

    def check(self, permit, *, now=None):
        self.checks += 1
        return permit.authority_proof


@dataclass(slots=True)
class Drawer:
    ready_error: Exception | None = None
    open_error: Exception | None = None
    opens: list[str] = field(default_factory=list)

    def ensure_ready(self, device_id: str) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    def open_cash_drawer(self, device_id: str) -> PrintJobResult:
        self.opens.append(device_id)
        if self.open_error is not None:
            raise self.open_error
        return PrintJobResult(
            printer=PrinterDevice(
                name="Receipt Printer",
                driver_key="tests.printer",
                identity=DeviceIdentity(
                    transport=DeviceTransport.SPOOLER,
                    os_instance_id="printer:test",
                ),
                preferred_transport=PrinterTransport.RAW,
                capabilities=PrinterCapabilities(raw=True, cash_drawer=True),
            ),
            transport=PrinterTransport.RAW,
            bytes_written=5,
        )


def authorization(*, pairing_id: str = "pairing_1") -> AuthorizedRequest:
    browser_origin = BoundOrigin("https://odoo.example")
    agent_endpoint = BoundOrigin("https://agent.example")
    business = BusinessScope(
        database="odoo",
        company_id="company_1",
        organization_id="org_1",
        site_id="site_1",
        pos_configuration_id="pos_1",
    )
    scope = PairingScope(
        agent_id="agent_1",
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        audience="inari.local",
    )
    target = RequestTarget("POST", "https://agent.example/v1/drawer-intents")
    grant = ClientGrant(
        grant_id="grant_1",
        pairing_id=pairing_id,
        jwk_thumbprint="thumbprint_1",
        scope=scope,
        actor_id="res.users:7",
        role="device_operator",
        permissions=frozenset({Permission.DRAWER, Permission.JOBS_READ}),
        authorization_digest="authorization_digest_1",
        generation=1,
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=14),
    )
    proof = AcceptedDPoPProof(
        jwk_thumbprint=grant.jwk_thumbprint,
        htm=target.method,
        htu=target.uri,
        iat=NOW,
        ath="access_hash_1",
        nonce="nonce_value_1",
        jti="proof_value_1",
        accepted_at=NOW,
    )
    endpoint = EndpointPolicy(
        agent_id=scope.agent_id,
        audience=scope.audience,
        browser_origin=browser_origin,
        agent_endpoint=agent_endpoint,
        business=business,
        allowed_methods=frozenset({"POST"}),
        allowed_paths=("/v1/drawer-intents", "/v1/drawer-intents/query"),
    )
    return AuthorizedRequest(
        target=target,
        grant=grant,
        dpop=proof,
        endpoint=endpoint,
        accepted_at=NOW,
    )


def request(**changes) -> DrawerIntentRequest:
    values = {
        "intent_id": "drawer_1",
        "device_id": "device_1",
        "binding_revision_id": "binding_1",
        "pos_session_id": "session_1",
        "action_sequence": 1,
        "reason": DrawerReason.PAYMENT,
    }
    values.update(changes)
    return DrawerIntentRequest(**values)


def service(tmp_path: Path):
    store = RuntimeStore(tmp_path / "agent.sqlite3")
    metadata.create_all(store.engine)
    ledger = SqliteDrawerIntentLedger(store)
    authority = Authority()
    drawer = Drawer()
    clock = Clock()
    return (
        DrawerIntentService(
            ledger=ledger,
            authority=authority,
            drawer=drawer,
            clock=clock,
        ),
        ledger,
        authority,
        drawer,
        clock,
    )


def test_exact_replay_never_pulses_twice(tmp_path: Path) -> None:
    intents, _, authority, drawer, _ = service(tmp_path)
    accepted = intents.submit(request(), authorization())

    intents.execute(accepted)
    replay = intents.submit(request(), authorization())
    intents.execute(replay)

    page = intents.query(("drawer_1",), authorization())
    assert drawer.opens == ["device_1"]
    assert authority.checks == 1
    assert replay.replayed is True
    assert replay.permit is None
    assert page.intents[0].state is DrawerIntentState.SUCCEEDED
    assert page.intents[0].state_version == 3
    assert page.intents[0].retryable is False


def test_changed_replay_and_changed_intent_for_one_action_conflict(
    tmp_path: Path,
) -> None:
    intents, _, _, _, _ = service(tmp_path)
    intents.submit(request(), authorization())

    with pytest.raises(DomainFailure) as changed_replay:
        intents.submit(request(reason=DrawerReason.MANUAL_OPEN), authorization())
    with pytest.raises(DomainFailure) as changed_identity:
        intents.submit(request(intent_id="drawer_2"), authorization())

    assert changed_replay.value.code is ProblemCode.IDEMPOTENCY_CONFLICT
    assert changed_identity.value.code is ProblemCode.IDEMPOTENCY_CONFLICT


def test_pre_io_failure_can_rearm_the_same_intent(tmp_path: Path) -> None:
    intents, _, _, drawer, _ = service(tmp_path)
    drawer.ready_error = AgentError("DEVICE_UNAVAILABLE", "offline")
    first = intents.submit(request(), authorization())
    intents.execute(first)
    failed = intents.query(("drawer_1",), authorization()).intents[0]
    assert failed.state is DrawerIntentState.FAILED
    assert failed.retryable is True

    drawer.ready_error = None
    retry = intents.submit(request(), authorization())
    intents.execute(retry)

    result = intents.query(("drawer_1",), authorization()).intents[0]
    assert retry.replayed is True
    assert drawer.opens == ["device_1"]
    assert result.state is DrawerIntentState.SUCCEEDED


def test_exception_after_io_marker_is_never_retried(tmp_path: Path) -> None:
    intents, _, _, drawer, _ = service(tmp_path)
    drawer.open_error = TimeoutError("unknown")
    first = intents.submit(request(), authorization())
    intents.execute(first)

    replay = intents.submit(request(), authorization())
    intents.execute(replay)
    result = intents.query(("drawer_1",), authorization()).intents[0]

    assert drawer.opens == ["device_1"]
    assert replay.permit is None
    assert result.state is DrawerIntentState.OUTCOME_UNKNOWN
    assert result.retryable is False


def test_stale_in_progress_becomes_outcome_unknown(tmp_path: Path) -> None:
    intents, ledger, _, _, clock = service(tmp_path)
    accepted = intents.submit(request(), authorization())
    ledger.mark_io_started(accepted.record.record_id, now=NOW)
    clock.value = NOW + timedelta(seconds=6)

    result = intents.query(("drawer_1",), authorization()).intents[0]

    assert result.state is DrawerIntentState.OUTCOME_UNKNOWN
    assert result.error_code == "outcome_unknown"


def test_query_hides_another_paired_client(tmp_path: Path) -> None:
    intents, _, _, _, _ = service(tmp_path)
    intents.submit(request(), authorization(pairing_id="pairing_1"))

    page = intents.query(("drawer_1",), authorization(pairing_id="pairing_2"))

    assert page.intents == ()
    assert page.missing_intent_ids == ("drawer_1",)


def test_action_collision_from_another_pairing_cannot_execute(tmp_path: Path) -> None:
    intents, _, _, drawer, _ = service(tmp_path)
    first = intents.submit(request(), authorization(pairing_id="pairing_1"))

    with pytest.raises(DomainFailure) as collision:
        intents.submit(request(), authorization(pairing_id="pairing_2"))

    intents.execute(first)
    assert collision.value.code is ProblemCode.IDEMPOTENCY_CONFLICT
    assert drawer.opens == ["device_1"]
