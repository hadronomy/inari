from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ...drawer_intents import (
    DrawerIntentRecord,
    DrawerIntentRequest,
    DrawerIntentState,
    DrawerReason,
)
from .base import APIModel


DrawerIntentId = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]
StableIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"),
]


class DrawerIntentSubmitRequest(APIModel):
    contract_major: Literal[1]
    drawer_intent_id: DrawerIntentId
    binding_revision_id: StableIdentifier
    device_id: StableIdentifier
    pos_session_id: StableIdentifier
    action_sequence: int = Field(ge=1, le=9_007_199_254_740_991)
    reason: Literal["payment", "manual_open"]

    def to_domain(self) -> DrawerIntentRequest:
        return DrawerIntentRequest(
            intent_id=self.drawer_intent_id,
            device_id=self.device_id,
            binding_revision_id=self.binding_revision_id,
            pos_session_id=self.pos_session_id,
            action_sequence=self.action_sequence,
            reason=DrawerReason(self.reason),
        )


class DrawerIntentSubmitResponse(APIModel):
    ok: Literal[True] = True
    drawer_intent_id: str
    device_id: str
    state: DrawerIntentState
    state_version: int
    retryable: bool
    accepted_at: datetime
    replayed: bool


class DrawerIntentQueryRequest(APIModel):
    drawer_intent_ids: list[DrawerIntentId] = Field(min_length=1, max_length=100)


class DrawerIntentResponse(APIModel):
    drawer_intent_id: str
    device_id: str
    state: DrawerIntentState
    state_version: int
    retryable: bool
    accepted_at: datetime
    started_at: datetime | None = None
    terminal_at: datetime | None = None
    error_code: str | None = None
    message_key: str | None = None
    printer_name: str | None = None
    transport: str | None = None

    @classmethod
    def from_domain(cls, intent: DrawerIntentRecord) -> DrawerIntentResponse:
        return cls(
            drawer_intent_id=intent.intent_id,
            device_id=intent.device_id,
            state=intent.state,
            state_version=intent.state_version,
            retryable=intent.retryable,
            accepted_at=intent.accepted_at,
            started_at=intent.started_at,
            terminal_at=intent.terminal_at,
            error_code=intent.error_code,
            message_key=intent.message_key,
            printer_name=intent.printer_name,
            transport=intent.transport,
        )


class DrawerIntentQueryResponse(APIModel):
    ok: Literal[True] = True
    intents: list[DrawerIntentResponse]
    missing_drawer_intent_ids: list[str]


__all__ = [
    "DrawerIntentQueryRequest",
    "DrawerIntentQueryResponse",
    "DrawerIntentResponse",
    "DrawerIntentSubmitRequest",
    "DrawerIntentSubmitResponse",
]
