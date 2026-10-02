from __future__ import annotations

import asyncio
import base64
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
import hashlib
import time
from typing import Any
from uuid import uuid4

from ..client_trust import AuthorizedRequest, ClientTrustError, Permission
from ..core.failures import DomainFailure, ProblemCode, ProblemDetails
from ..device_authority import (
    AdmissionPermit,
    AuthorityError,
    AuthorityErrorCode,
    AuthorityScope,
    CapabilityStreamTarget,
    ScopeKind,
    canonical_json_bytes,
)
from .models import (
    BarcodeSample,
    DeviceStreamKind,
    EventLease,
    EventLeaseRequest,
    ScaleLease,
    ScaleSample,
    SignedStreamMessage,
    StreamMessageKind,
    StreamSelection,
)
from .ports import DeviceStreamLedger, StreamAuthority, StreamSigner


_EVENT_LEASE_TTL = timedelta(seconds=10)
_SCALE_LEASE_TTL = timedelta(seconds=3)
_HEARTBEAT_INTERVAL = timedelta(seconds=15)
_MAX_SCANNER_REPLAY = 128
_MAX_SCALE_AGE = timedelta(seconds=1)
_MAX_BARCODE_AGE = timedelta(seconds=1)
_MAX_FUTURE_SKEW = timedelta(milliseconds=250)
_MIN_SCALE_INTERVAL_SECONDS = 0.5


@dataclass(slots=True)
class _LeaseState:
    lease: EventLease
    scope_digest: str
    pairing_id: str
    jwk_thumbprint: str
    grant_generation: int
    authorization_digest: str
    permits: dict[str, AdmissionPermit]
    next_stream_sequence: int = 0
    scanner_replay: deque[SignedStreamMessage] = field(default_factory=deque)
    scanner_acks: dict[str, int] = field(default_factory=dict)
    pending_scale: dict[str, SignedStreamMessage] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    stream_connected: bool = False
    terminal_reason: str | None = None
    scale_loss_notified: bool = False


@dataclass(slots=True)
class _ScaleLeaseState:
    lease: ScaleLease
    pairing_id: str
    authorization_digest: str


class DeviceStreamService:
    """Own authenticated POS input leases and signed typed Device Streams."""

    def __init__(
        self,
        *,
        ledger: DeviceStreamLedger,
        authority: StreamAuthority,
        signer: StreamSigner,
        input_kinds: frozenset[DeviceStreamKind],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        agent_boot_id: str | None = None,
        event_lease_ttl: timedelta = _EVENT_LEASE_TTL,
        scale_lease_ttl: timedelta = _SCALE_LEASE_TTL,
        heartbeat_interval: timedelta = _HEARTBEAT_INTERVAL,
    ) -> None:
        if event_lease_ttl <= timedelta(0):
            raise ValueError("event_lease_ttl must be positive")
        if scale_lease_ttl <= timedelta(0):
            raise ValueError("scale_lease_ttl must be positive")
        if heartbeat_interval <= timedelta(0):
            raise ValueError("heartbeat_interval must be positive")
        self._ledger = ledger
        self._authority = authority
        self._signer = signer
        self._input_kinds = input_kinds
        self._clock = clock
        self._monotonic = monotonic
        self._agent_boot_id = agent_boot_id or f"boot_{uuid4().hex}"
        self._event_lease_ttl = event_lease_ttl
        self._scale_lease_ttl = scale_lease_ttl
        self._heartbeat_interval = heartbeat_interval
        self._lock = asyncio.Lock()
        self._leases: dict[str, _LeaseState] = {}
        self._lease_by_scope: dict[str, str] = {}
        self._scale_leases: dict[str, _ScaleLeaseState] = {}
        self._scale_by_device: dict[str, str] = {}
        self._device_sequences: dict[str, int] = {}
        self._last_scale_emitted: dict[str, float] = {}

    @property
    def agent_boot_id(self) -> str:
        return self._agent_boot_id

    @property
    def signing_public_jwk(self) -> Mapping[str, str]:
        return self._signer.public_jwk

    async def acquire(
        self,
        request: EventLeaseRequest,
        authorization: AuthorizedRequest,
    ) -> EventLease:
        self._require(authorization, Permission.EVENTS_READ)
        scope = _authority_scope(authorization)
        scope_digest = _scope_digest(authorization)
        permits = self._authorize_selections(request.selections, authorization, scope)
        now = _utc(self._clock())

        async with self._lock:
            self._purge_expired(now)
            existing_id = self._lease_by_scope.get(scope_digest)
            if existing_id is not None:
                existing = self._leases[existing_id]
                if (
                    existing.lease.holder_id == request.holder_id
                    and existing.lease.selections == request.selections
                    and _authorization_matches(existing, authorization)
                ):
                    return existing.lease
                raise DomainFailure(
                    ProblemCode.TRANSPORT_LEADER_ACTIVE,
                    details=ProblemDetails(retry_after_seconds=3),
                )

            generation = self._ledger.next_generation(scope_digest)
            lease = EventLease(
                lease_id=f"lease_{uuid4().hex}",
                subscription_id=f"sub_{uuid4().hex}",
                holder_id=request.holder_id,
                generation=generation,
                scope_digest=scope_digest,
                agent_id=self._signer.agent_id,
                agent_boot_id=self._agent_boot_id,
                client_grant_id=authorization.grant.grant_id,
                selections=request.selections,
                issued_at=now,
                expires_at=now + self._event_lease_ttl,
                renew_after_ms=3_000,
                heartbeat_interval_ms=int(
                    self._heartbeat_interval.total_seconds() * 1_000
                ),
            )
            state = _LeaseState(
                lease=lease,
                scope_digest=scope_digest,
                pairing_id=authorization.grant.pairing_id,
                jwk_thumbprint=authorization.grant.jwk_thumbprint,
                grant_generation=authorization.grant.generation,
                authorization_digest=authorization.grant.authorization_digest,
                permits=permits,
            )
            self._leases[lease.lease_id] = state
            self._lease_by_scope[scope_digest] = lease.lease_id
            return lease

    async def renew(
        self,
        lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
    ) -> EventLease:
        self._require(authorization, Permission.EVENTS_READ)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._current_lease(lease_id, generation, authorization, now)
            selections = state.lease.selections
        permits = self._authorize_selections(
            selections,
            authorization,
            _authority_scope(authorization),
        )
        async with self._lock:
            state = self._current_lease(lease_id, generation, authorization, now)
            state.permits = permits
            state.lease = replace(
                state.lease,
                client_grant_id=authorization.grant.grant_id,
                expires_at=now + self._event_lease_ttl,
            )
            state.changed.set()
            return state.lease

    async def release(
        self,
        lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
    ) -> None:
        self._require(authorization, Permission.EVENTS_READ)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._leases.get(lease_id)
            if state is None:
                return
            self._check_lease(state, generation, authorization, now)
            self._end_lease(state, "released")

    async def acquire_scale_lease(
        self,
        event_lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
    ) -> ScaleLease:
        self._require(authorization, Permission.SCALE)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            event_state = self._current_lease(
                event_lease_id, generation, authorization, now
            )
            selection = _selection(event_state.lease, DeviceStreamKind.SCALE)
            current_id = self._scale_by_device.get(selection.device_id)
            if current_id is not None:
                current = self._scale_leases[current_id]
                if current.lease.event_lease_id == event_lease_id:
                    return current.lease
                raise DomainFailure(
                    ProblemCode.SCALE_IN_USE,
                    details=ProblemDetails(
                        device_id=selection.device_id,
                        retry_after_seconds=3,
                    ),
                )
            scale_scope = f"scale:{selection.device_id}:{selection.binding_revision_id}"
            scale_generation = self._ledger.next_generation(scale_scope)
            lease = ScaleLease(
                scale_lease_id=f"scale_{uuid4().hex}",
                event_lease_id=event_lease_id,
                device_id=selection.device_id,
                binding_revision_id=selection.binding_revision_id,
                generation=scale_generation,
                issued_at=now,
                expires_at=now + self._scale_lease_ttl,
            )
            self._scale_leases[lease.scale_lease_id] = _ScaleLeaseState(
                lease=lease,
                pairing_id=authorization.grant.pairing_id,
                authorization_digest=authorization.grant.authorization_digest,
            )
            self._scale_by_device[selection.device_id] = lease.scale_lease_id
            event_state.scale_loss_notified = False
            event_state.changed.set()
            return lease

    async def renew_scale_lease(
        self,
        scale_lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
    ) -> ScaleLease:
        self._require(authorization, Permission.SCALE)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._current_scale_lease(
                scale_lease_id, generation, authorization, now
            )
            event_state = self._leases.get(state.lease.event_lease_id)
            if event_state is None:
                raise DomainFailure(ProblemCode.TRANSPORT_LEADER_LOST)
            self._check_lease(
                event_state, event_state.lease.generation, authorization, now
            )
            state.lease = replace(
                state.lease,
                expires_at=now + self._scale_lease_ttl,
            )
            event_state.changed.set()
            return state.lease

    async def release_scale_lease(
        self,
        scale_lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
    ) -> None:
        self._require(authorization, Permission.SCALE)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._scale_leases.get(scale_lease_id)
            if state is None:
                return
            self._check_scale_lease(state, generation, authorization, now)
            self._end_scale_lease(state)

    async def acknowledge(
        self,
        lease_id: str,
        subscription_id: str,
        generation: int,
        acknowledgements: Mapping[str, int],
        authorization: AuthorizedRequest,
    ) -> None:
        self._require(authorization, Permission.SCANNER)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._current_lease(lease_id, generation, authorization, now)
            if state.lease.subscription_id != subscription_id:
                raise DomainFailure(ProblemCode.TRANSPORT_LEADER_LOST)
            delivered: dict[str, int] = {}
            for message in state.scanner_replay:
                device_id = str(message.payload["device_id"])
                delivered[device_id] = max(
                    delivered.get(device_id, 0),
                    _payload_sequence(message),
                )
            for device_id, sequence in acknowledgements.items():
                prior = state.scanner_acks.get(device_id, 0)
                if sequence < prior:
                    continue
                if sequence > max(prior, delivered.get(device_id, -1)):
                    raise DomainFailure(ProblemCode.REQUEST_CONFLICT)
                state.scanner_acks[device_id] = sequence
            state.scanner_replay = deque(
                message
                for message in state.scanner_replay
                if _payload_sequence(message)
                > acknowledgements.get(str(message.payload["device_id"]), -1)
            )

    async def publish_scale(self, sample: ScaleSample) -> int:
        if not isinstance(sample, ScaleSample):
            raise TypeError("sample must be a ScaleSample")
        now = _utc(self._clock())
        if not _fresh(sample.observed_at, now, _MAX_SCALE_AGE):
            return 0
        monotonic_now = self._monotonic()
        async with self._lock:
            self._purge_expired(now)
            last = self._last_scale_emitted.get(sample.device_id)
            if last is not None and monotonic_now - last < _MIN_SCALE_INTERVAL_SECONDS:
                return 0
            targets = [
                state
                for state in self._leases.values()
                if _matches_sample(state.lease, DeviceStreamKind.SCALE, sample)
                and self._has_current_scale_lease(state, sample, now)
            ]
            if not targets:
                return 0
            valid_targets = self._check_authority(targets, sample.device_id, now)
            if not valid_targets:
                return 0
            self._last_scale_emitted[sample.device_id] = monotonic_now
            device_sequence = self._next_device_sequence(sample.device_id)
            agent_sequence = self._ledger.next_sequence()
            for state in valid_targets:
                selection = _selection(state.lease, DeviceStreamKind.SCALE)
                proof = state.permits[sample.device_id].authority_proof
                if selection.certification_id != proof.matrix_row_id:
                    state.terminal_reason = "certification_changed"
                    state.changed.set()
                    continue
                message = self._data_message(
                    state,
                    StreamMessageKind.SCALE_READING,
                    now,
                    {
                        **self._identity_payload(state, selection),
                        "agent_sequence": agent_sequence,
                        "sequence": device_sequence,
                        "observed_at": _timestamp(sample.observed_at),
                        "monotonic_ms": sample.monotonic_ms,
                        "value_mantissa": str(sample.value_mantissa),
                        "decimal_exponent": sample.decimal_exponent,
                        "unit": sample.unit,
                        "resolution_mantissa": str(sample.resolution_mantissa),
                        "stable": sample.stable,
                        "range_state": sample.range_state.value,
                        "certification_id": proof.matrix_row_id,
                    },
                )
                state.pending_scale[sample.device_id] = message
                state.changed.set()
            return len(valid_targets)

    async def publish_barcode(self, sample: BarcodeSample) -> int:
        if not isinstance(sample, BarcodeSample):
            raise TypeError("sample must be a BarcodeSample")
        now = _utc(self._clock())
        if not _fresh(sample.observed_at, now, _MAX_BARCODE_AGE):
            return 0
        async with self._lock:
            self._purge_expired(now)
            targets = [
                state
                for state in self._leases.values()
                if _matches_sample(state.lease, DeviceStreamKind.SCANNER, sample)
            ]
            valid_targets = self._check_authority(targets, sample.device_id, now)
            if not valid_targets:
                return 0
            device_sequence = self._next_device_sequence(sample.device_id)
            agent_sequence = self._ledger.next_sequence()
            delivered = 0
            for state in valid_targets:
                if len(state.scanner_replay) >= _MAX_SCANNER_REPLAY:
                    state.terminal_reason = "buffer_overflow"
                    state.scanner_replay.clear()
                    state.changed.set()
                    continue
                selection = _selection(state.lease, DeviceStreamKind.SCANNER)
                message = self._data_message(
                    state,
                    StreamMessageKind.BARCODE,
                    now,
                    {
                        **self._identity_payload(state, selection),
                        "agent_sequence": agent_sequence,
                        "sequence": device_sequence,
                        "observed_at": _timestamp(sample.observed_at),
                        "monotonic_ms": sample.monotonic_ms,
                        "symbology": sample.symbology,
                        "value": sample.value,
                    },
                )
                state.scanner_replay.append(message)
                state.changed.set()
                delivered += 1
            return delivered

    async def stream(
        self,
        lease_id: str,
        subscription_id: str,
        generation: int,
        authorization: AuthorizedRequest,
        *,
        scale_lease_id: str | None,
        last_event_id: int | None = None,
    ) -> AsyncGenerator[SignedStreamMessage, None]:
        self._require(authorization, Permission.EVENTS_READ)
        cursor = last_event_id or 0
        if cursor < 0:
            raise DomainFailure(ProblemCode.REQUEST_MALFORMED)
        now = _utc(self._clock())
        async with self._lock:
            self._purge_expired(now)
            state = self._current_lease(lease_id, generation, authorization, now)
            if state.lease.subscription_id != subscription_id:
                raise DomainFailure(ProblemCode.TRANSPORT_LEADER_LOST)
            if state.stream_connected:
                raise DomainFailure(ProblemCode.TRANSPORT_LEADER_ACTIVE)
            if _has_kind(state.lease, DeviceStreamKind.SCALE):
                if scale_lease_id is None:
                    raise DomainFailure(ProblemCode.SCALE_LEASE_REQUIRED)
                scale_state = self._scale_leases.get(scale_lease_id)
                if scale_state is None:
                    raise DomainFailure(ProblemCode.SCALE_LEASE_REQUIRED)
                self._check_scale_lease(
                    scale_state,
                    scale_state.lease.generation,
                    authorization,
                    now,
                )
                if scale_state.lease.event_lease_id != lease_id:
                    raise DomainFailure(ProblemCode.SCALE_LEASE_REQUIRED)
            state.stream_connected = True
            high_water = self._ledger.current_sequence()
            ready = self._control_message(
                state,
                StreamMessageKind.READY,
                now,
                {
                    "lease_id": state.lease.lease_id,
                    "subscription_id": state.lease.subscription_id,
                    "scope_digest": state.scope_digest,
                    "agent_id": self._signer.agent_id,
                    "agent_boot_id": self._agent_boot_id,
                    "client_grant_id": state.lease.client_grant_id,
                    "current_sequence": state.next_stream_sequence,
                    "high_water_mark": high_water,
                    "device_cursors": dict(self._device_sequences),
                    "signing_public_jwk": dict(self._signer.public_jwk),
                },
            )

        last_heartbeat = now
        try:
            yield ready
            while True:
                wait_seconds = 0.1
                message: SignedStreamMessage | None = None
                should_close = False
                async with self._lock:
                    now = _utc(self._clock())
                    self._purge_expired(now)
                    current = self._leases.get(lease_id)
                    if current is None:
                        current = state
                        reason = current.terminal_reason or "expired"
                        message = self._control_message(
                            current,
                            StreamMessageKind.LEASE_LOST,
                            now,
                            {"reason": reason},
                        )
                        should_close = True
                    elif current.terminal_reason is not None:
                        kind = (
                            StreamMessageKind.REPLAY_UNAVAILABLE
                            if current.terminal_reason == "buffer_overflow"
                            else StreamMessageKind.LEASE_LOST
                        )
                        message = self._control_message(
                            current,
                            kind,
                            now,
                            {"reason": current.terminal_reason},
                        )
                        self._end_lease(current, current.terminal_reason)
                        should_close = True
                    else:
                        scale_message = self._scale_loss_message(current, now)
                        if scale_message is not None:
                            message = scale_message
                        else:
                            candidates = [
                                item
                                for item in current.scanner_replay
                                if item.stream_sequence > cursor
                            ]
                            candidates.extend(
                                item
                                for item in current.pending_scale.values()
                                if item.stream_sequence > cursor
                            )
                            if candidates:
                                message = min(
                                    candidates, key=lambda item: item.stream_sequence
                                )
                                if message.kind is StreamMessageKind.SCALE_READING:
                                    current.pending_scale.pop(
                                        str(message.payload["device_id"]), None
                                    )
                                cursor = message.stream_sequence
                            elif now - last_heartbeat >= self._heartbeat_interval:
                                message = self._control_message(
                                    current,
                                    StreamMessageKind.HEARTBEAT,
                                    now,
                                    {
                                        "subscription_id": current.lease.subscription_id,
                                        "agent_boot_id": self._agent_boot_id,
                                    },
                                )
                                last_heartbeat = now
                            else:
                                current.changed.clear()
                                lease_remaining = max(
                                    0.01,
                                    (current.lease.expires_at - now).total_seconds(),
                                )
                                heartbeat_remaining = max(
                                    0.01,
                                    (
                                        self._heartbeat_interval
                                        - (now - last_heartbeat)
                                    ).total_seconds(),
                                )
                                wait_seconds = min(lease_remaining, heartbeat_remaining)
                if message is not None:
                    yield message
                    if should_close:
                        return
                    continue
                try:
                    await asyncio.wait_for(state.changed.wait(), timeout=wait_seconds)
                except TimeoutError:
                    pass
        finally:
            async with self._lock:
                state.stream_connected = False

    def _authorize_selections(
        self,
        selections: tuple[StreamSelection, ...],
        authorization: AuthorizedRequest,
        scope: AuthorityScope,
    ) -> dict[str, AdmissionPermit]:
        permits: dict[str, AdmissionPermit] = {}
        now = _utc(self._clock())
        for selection in selections:
            if selection.kind not in self._input_kinds:
                raise DomainFailure(ProblemCode.DEVICE_UNAVAILABLE)
            permission, purpose, operation = {
                DeviceStreamKind.SCALE: (
                    Permission.SCALE,
                    "pos_scale",
                    "scale_reading",
                ),
                DeviceStreamKind.SCANNER: (
                    Permission.SCANNER,
                    "pos_scanner",
                    "barcode_event",
                ),
            }[selection.kind]
            self._require(authorization, permission)
            try:
                permit = self._authority.authorize_stream(
                    CapabilityStreamTarget(
                        scope=scope,
                        purpose=purpose,
                        device_id=selection.device_id,
                        binding_revision_id=selection.binding_revision_id,
                        operation=operation,
                        contract_major=1,
                    ),
                    now=now,
                )
            except AuthorityError as error:
                raise DomainFailure(_authority_problem(error.code)) from error
            if (
                selection.kind is DeviceStreamKind.SCALE
                and permit.authority_proof.matrix_row_id != selection.certification_id
            ):
                raise DomainFailure(ProblemCode.CERTIFICATION_REQUIRED)
            permits[selection.device_id] = permit
        return permits

    def _check_authority(
        self,
        states: list[_LeaseState],
        device_id: str,
        now: datetime,
    ) -> list[_LeaseState]:
        valid: list[_LeaseState] = []
        for state in states:
            try:
                self._authority.check(state.permits[device_id], now=now)
            except (AuthorityError, KeyError):
                state.terminal_reason = "authority_changed"
                state.changed.set()
            else:
                valid.append(state)
        return valid

    def _current_lease(
        self,
        lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
        now: datetime,
    ) -> _LeaseState:
        state = self._leases.get(lease_id)
        if state is None:
            raise DomainFailure(ProblemCode.TRANSPORT_LEADER_LOST)
        self._check_lease(state, generation, authorization, now)
        return state

    @staticmethod
    def _check_lease(
        state: _LeaseState,
        generation: int,
        authorization: AuthorizedRequest,
        now: datetime,
    ) -> None:
        if (
            state.lease.generation != generation
            or state.lease.expires_at <= now
            or not _authorization_matches(state, authorization)
        ):
            raise DomainFailure(ProblemCode.TRANSPORT_LEADER_LOST)

    def _current_scale_lease(
        self,
        scale_lease_id: str,
        generation: int,
        authorization: AuthorizedRequest,
        now: datetime,
    ) -> _ScaleLeaseState:
        state = self._scale_leases.get(scale_lease_id)
        if state is None:
            raise DomainFailure(ProblemCode.SCALE_LEASE_REQUIRED)
        self._check_scale_lease(state, generation, authorization, now)
        return state

    @staticmethod
    def _check_scale_lease(
        state: _ScaleLeaseState,
        generation: int,
        authorization: AuthorizedRequest,
        now: datetime,
    ) -> None:
        if (
            state.lease.generation != generation
            or state.lease.expires_at <= now
            or state.pairing_id != authorization.grant.pairing_id
            or state.authorization_digest != authorization.grant.authorization_digest
        ):
            raise DomainFailure(ProblemCode.SCALE_LEASE_REQUIRED)

    def _purge_expired(self, now: datetime) -> None:
        for state in tuple(self._scale_leases.values()):
            if state.lease.expires_at <= now:
                self._end_scale_lease(state)
        for state in tuple(self._leases.values()):
            if state.lease.expires_at <= now:
                self._end_lease(state, "expired")

    def _end_lease(self, state: _LeaseState, reason: str) -> None:
        self._leases.pop(state.lease.lease_id, None)
        if self._lease_by_scope.get(state.scope_digest) == state.lease.lease_id:
            self._lease_by_scope.pop(state.scope_digest, None)
        for scale_state in tuple(self._scale_leases.values()):
            if scale_state.lease.event_lease_id == state.lease.lease_id:
                self._end_scale_lease(scale_state)
        state.terminal_reason = reason
        state.scanner_replay.clear()
        state.pending_scale.clear()
        state.changed.set()

    def _end_scale_lease(self, state: _ScaleLeaseState) -> None:
        self._scale_leases.pop(state.lease.scale_lease_id, None)
        if (
            self._scale_by_device.get(state.lease.device_id)
            == state.lease.scale_lease_id
        ):
            self._scale_by_device.pop(state.lease.device_id, None)
        event_state = self._leases.get(state.lease.event_lease_id)
        if event_state is not None:
            event_state.pending_scale.pop(state.lease.device_id, None)
            event_state.changed.set()

    def _has_current_scale_lease(
        self,
        state: _LeaseState,
        sample: ScaleSample,
        now: datetime,
    ) -> bool:
        scale_id = self._scale_by_device.get(sample.device_id)
        scale_state = self._scale_leases.get(scale_id or "")
        return bool(
            scale_state
            and scale_state.lease.event_lease_id == state.lease.lease_id
            and scale_state.lease.binding_revision_id == sample.binding_revision_id
            and scale_state.lease.expires_at > now
        )

    def _scale_loss_message(
        self, state: _LeaseState, now: datetime
    ) -> SignedStreamMessage | None:
        if state.scale_loss_notified or not _has_kind(
            state.lease, DeviceStreamKind.SCALE
        ):
            return None
        selection = _selection(state.lease, DeviceStreamKind.SCALE)
        scale_id = self._scale_by_device.get(selection.device_id)
        scale_state = self._scale_leases.get(scale_id or "")
        if (
            scale_state is not None
            and scale_state.lease.event_lease_id == state.lease.lease_id
        ):
            return None
        state.scale_loss_notified = True
        return self._control_message(
            state,
            StreamMessageKind.SCALE_LEASE_LOST,
            now,
            {"device_id": selection.device_id, "reason": "expired"},
        )

    def _data_message(
        self,
        state: _LeaseState,
        kind: StreamMessageKind,
        now: datetime,
        payload: Mapping[str, object],
    ) -> SignedStreamMessage:
        state.next_stream_sequence += 1
        return self._signed_message(
            kind=kind,
            stream_sequence=state.next_stream_sequence,
            generation=state.lease.generation,
            occurred_at=now,
            payload=payload,
        )

    def _control_message(
        self,
        state: _LeaseState,
        kind: StreamMessageKind,
        now: datetime,
        payload: Mapping[str, object],
    ) -> SignedStreamMessage:
        return self._signed_message(
            kind=kind,
            stream_sequence=state.next_stream_sequence,
            generation=state.lease.generation,
            occurred_at=now,
            payload={
                "lease_id": state.lease.lease_id,
                "subscription_id": state.lease.subscription_id,
                "scope_digest": state.scope_digest,
                **dict(payload),
            },
        )

    def _signed_message(
        self,
        *,
        kind: StreamMessageKind,
        stream_sequence: int,
        generation: int,
        occurred_at: datetime,
        payload: Mapping[str, object],
    ) -> SignedStreamMessage:
        unsigned = {
            "contract_major": 1,
            "kind": kind.value,
            "stream_sequence": stream_sequence,
            "generation": generation,
            "occurred_at": _timestamp(occurred_at),
            "payload": dict(payload),
            "signer_key_id": self._signer.key_id,
        }
        return SignedStreamMessage(
            kind=kind,
            stream_sequence=stream_sequence,
            generation=generation,
            occurred_at=occurred_at,
            payload=payload,
            signer_key_id=self._signer.key_id,
            signature=self._signer.sign(unsigned),
        )

    def _identity_payload(
        self, state: _LeaseState, selection: StreamSelection
    ) -> dict[str, Any]:
        return {
            "lease_id": state.lease.lease_id,
            "subscription_id": state.lease.subscription_id,
            "scope_digest": state.scope_digest,
            "agent_id": self._signer.agent_id,
            "agent_boot_id": self._agent_boot_id,
            "client_grant_id": state.lease.client_grant_id,
            "device_id": selection.device_id,
            "binding_revision_id": selection.binding_revision_id,
        }

    def _next_device_sequence(self, device_id: str) -> int:
        sequence = self._device_sequences.get(device_id, 0) + 1
        self._device_sequences[device_id] = sequence
        return sequence

    @staticmethod
    def _require(
        authorization: AuthorizedRequest,
        permission: Permission,
    ) -> None:
        try:
            authorization.require(permission)
        except ClientTrustError as error:
            raise DomainFailure(ProblemCode.PERMISSION_DENIED) from error


def _authority_scope(authorization: AuthorizedRequest) -> AuthorityScope:
    business = authorization.grant.scope.business
    if business.pos_configuration_id is None:
        raise DomainFailure(ProblemCode.BINDING_REQUIRED)
    return AuthorityScope(
        database=business.database,
        organization_id=business.organization_id,
        site_id=business.site_id,
        kind=ScopeKind.POS_CONFIGURATION,
        pos_configuration_id=business.pos_configuration_id,
    )


def _scope_digest(authorization: AuthorizedRequest) -> str:
    grant = authorization.grant
    business = grant.scope.business
    digest = hashlib.sha256(
        canonical_json_bytes(
            {
                "agent_id": grant.scope.agent_id,
                "database": business.database,
                "company_id": business.company_id,
                "organization_id": business.organization_id,
                "site_id": business.site_id,
                "pos_configuration_id": business.pos_configuration_id,
                "pairing_id": grant.pairing_id,
                "authorization_digest": grant.authorization_digest,
            }
        )
    ).digest()
    return "scope_" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _authorization_matches(
    state: _LeaseState,
    authorization: AuthorizedRequest,
) -> bool:
    grant = authorization.grant
    return (
        state.scope_digest == _scope_digest(authorization)
        and state.pairing_id == grant.pairing_id
        and state.jwk_thumbprint == grant.jwk_thumbprint
        and state.grant_generation == grant.generation
        and state.authorization_digest == grant.authorization_digest
    )


def _selection(lease: EventLease, kind: DeviceStreamKind) -> StreamSelection:
    for value in lease.selections:
        if value.kind is kind:
            return value
    raise DomainFailure(ProblemCode.BINDING_REQUIRED)


def _has_kind(lease: EventLease, kind: DeviceStreamKind) -> bool:
    return any(value.kind is kind for value in lease.selections)


def _matches_sample(
    lease: EventLease,
    kind: DeviceStreamKind,
    sample: ScaleSample | BarcodeSample,
) -> bool:
    return any(
        selection.kind is kind
        and selection.device_id == sample.device_id
        and selection.binding_revision_id == sample.binding_revision_id
        for selection in lease.selections
    )


def _payload_sequence(message: SignedStreamMessage) -> int:
    value = message.payload.get("sequence")
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError("A buffered Device Stream message has no sequence.")
    return value


def _fresh(observed_at: datetime, now: datetime, max_age: timedelta) -> bool:
    return now - max_age <= observed_at <= now + _MAX_FUTURE_SKEW


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _authority_problem(code: AuthorityErrorCode) -> ProblemCode:
    return {
        AuthorityErrorCode.INVALID_REQUEST: ProblemCode.PAYLOAD_INVALID,
        AuthorityErrorCode.SCOPE_MISMATCH: ProblemCode.PERMISSION_DENIED,
        AuthorityErrorCode.NOT_FOUND: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.SIGNATURE_INVALID: ProblemCode.SERVICE_UNAVAILABLE,
        AuthorityErrorCode.SIGNER_PURPOSE_MISMATCH: ProblemCode.SERVICE_UNAVAILABLE,
        AuthorityErrorCode.REVOKED: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.EXPIRED: ProblemCode.EXPIRED,
        AuthorityErrorCode.GRAPH_MISMATCH: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.CERTIFICATION_REQUIRED: ProblemCode.CERTIFICATION_REQUIRED,
        AuthorityErrorCode.TEST_REQUIRED: ProblemCode.CERTIFICATION_REQUIRED,
        AuthorityErrorCode.OBSERVATION_UNAVAILABLE: ProblemCode.DEVICE_UNAVAILABLE,
        AuthorityErrorCode.OBSERVATION_DRIFT: ProblemCode.CAPABILITY_CHANGED,
        AuthorityErrorCode.DEVICE_NOT_READY: ProblemCode.DEVICE_UNAVAILABLE,
        AuthorityErrorCode.AUTHORITY_UNAVAILABLE: ProblemCode.SERVICE_UNAVAILABLE,
    }[code]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Device Stream clock must return a timezone-aware value")
    return value.astimezone(UTC)


__all__ = ["DeviceStreamService"]
