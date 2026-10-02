from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ...device_streams import (
    DeviceStreamKind,
    EventLease,
    EventLeaseRequest,
    ScaleLease,
    StreamSelection,
)
from .base import APIModel


StableIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
HolderIdentity = Annotated[
    str,
    Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
SafeSequence = Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]


class StreamSelectionInput(APIModel):
    kind: Literal["scale", "scanner"]
    device_id: StableIdentifier
    binding_revision_id: StableIdentifier
    certification_id: StableIdentifier | None = None

    @model_validator(mode="after")
    def check_shape(self) -> StreamSelectionInput:
        if self.kind == "scale" and self.certification_id is None:
            raise ValueError("a scale selection requires certification_id")
        if self.kind == "scanner" and self.certification_id is not None:
            raise ValueError("a scanner selection cannot contain certification_id")
        return self

    def to_domain(self) -> StreamSelection:
        return StreamSelection(
            kind=DeviceStreamKind(self.kind),
            device_id=self.device_id,
            binding_revision_id=self.binding_revision_id,
            certification_id=self.certification_id,
        )


class EventLeaseAcquireRequest(APIModel):
    contract_major: Literal[1]
    holder_id: HolderIdentity
    selections: list[StreamSelectionInput] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def check_selection_uniqueness(self) -> EventLeaseAcquireRequest:
        kinds = [value.kind for value in self.selections]
        devices = [value.device_id for value in self.selections]
        if len(kinds) != len(set(kinds)) or len(devices) != len(set(devices)):
            raise ValueError("selections cannot repeat a kind or Device")
        return self

    def to_domain(self) -> EventLeaseRequest:
        return EventLeaseRequest(
            holder_id=self.holder_id,
            selections=tuple(value.to_domain() for value in self.selections),
        )


class EventLeaseControlRequest(APIModel):
    contract_major: Literal[1]
    lease_id: StableIdentifier
    generation: SafeSequence


class ScaleLeaseAcquireRequest(APIModel):
    contract_major: Literal[1]
    event_lease_id: StableIdentifier
    event_generation: SafeSequence


class ScaleLeaseControlRequest(APIModel):
    contract_major: Literal[1]
    scale_lease_id: StableIdentifier
    generation: SafeSequence


class EventLeaseResponse(APIModel):
    ok: Literal[True] = True
    lease_id: str
    subscription_id: str
    holder_id: str
    generation: int
    scope_digest: str
    agent_id: str
    agent_boot_id: str
    client_grant_id: str
    selections: list[StreamSelectionInput]
    issued_at: datetime
    expires_at: datetime
    renew_after_ms: int
    heartbeat_interval_ms: int
    signing_public_jwk: dict[str, str]

    @classmethod
    def from_domain(
        cls,
        lease: EventLease,
        *,
        signing_public_jwk: dict[str, str],
    ) -> EventLeaseResponse:
        return cls(
            lease_id=lease.lease_id,
            subscription_id=lease.subscription_id,
            holder_id=lease.holder_id,
            generation=lease.generation,
            scope_digest=lease.scope_digest,
            agent_id=lease.agent_id,
            agent_boot_id=lease.agent_boot_id,
            client_grant_id=lease.client_grant_id,
            selections=[
                StreamSelectionInput(
                    kind=selection.kind.value,
                    device_id=selection.device_id,
                    binding_revision_id=selection.binding_revision_id,
                    certification_id=selection.certification_id,
                )
                for selection in lease.selections
            ],
            issued_at=lease.issued_at,
            expires_at=lease.expires_at,
            renew_after_ms=lease.renew_after_ms,
            heartbeat_interval_ms=lease.heartbeat_interval_ms,
            signing_public_jwk=signing_public_jwk,
        )


class ScaleLeaseResponse(APIModel):
    ok: Literal[True] = True
    scale_lease_id: str
    event_lease_id: str
    device_id: str
    binding_revision_id: str
    generation: int
    issued_at: datetime
    expires_at: datetime
    renew_after_ms: int

    @classmethod
    def from_domain(cls, lease: ScaleLease) -> ScaleLeaseResponse:
        return cls(
            scale_lease_id=lease.scale_lease_id,
            event_lease_id=lease.event_lease_id,
            device_id=lease.device_id,
            binding_revision_id=lease.binding_revision_id,
            generation=lease.generation,
            issued_at=lease.issued_at,
            expires_at=lease.expires_at,
            renew_after_ms=lease.renew_after_ms,
        )


class BarcodeAcknowledgementInput(APIModel):
    device_id: StableIdentifier
    sequence: SafeSequence


class EventAcknowledgementRequest(EventLeaseControlRequest):
    subscription_id: StableIdentifier
    acknowledgements: list[BarcodeAcknowledgementInput] = Field(
        min_length=1, max_length=16
    )

    @model_validator(mode="after")
    def check_devices(self) -> EventAcknowledgementRequest:
        devices = [value.device_id for value in self.acknowledgements]
        if len(devices) != len(set(devices)):
            raise ValueError("acknowledgements cannot repeat a Device")
        return self


class EmptySuccessResponse(APIModel):
    ok: Literal[True] = True


__all__ = [
    "EmptySuccessResponse",
    "EventAcknowledgementRequest",
    "EventLeaseAcquireRequest",
    "EventLeaseControlRequest",
    "EventLeaseResponse",
    "ScaleLeaseControlRequest",
    "ScaleLeaseAcquireRequest",
    "ScaleLeaseResponse",
    "StreamSelectionInput",
]
