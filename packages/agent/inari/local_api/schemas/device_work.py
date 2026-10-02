from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import APIModel


class PosPrintOriginInput(APIModel):
    pos_session_id: str = Field(min_length=1, max_length=128)
    offline_order_id: str = Field(min_length=1, max_length=128)
    server_order_id: str | None = Field(default=None, max_length=128)
    document_kind: str = Field(min_length=1, max_length=64)
    content_revision: str = Field(min_length=1, max_length=256)


class SubmissionContextInput(APIModel):
    contract_major: Literal[1]
    print_intent_id: str = Field(min_length=1, max_length=256)
    origin_submission_key: str = Field(min_length=1, max_length=256)
    origin: PosPrintOriginInput
    binding_revision_id: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=256)
    copy_ordinal: int = Field(ge=1)


class ReceiptImageEnvelope(APIModel):
    """Canonical JSON metadata for one local receipt-image submission."""

    contract_major: Literal[1]
    operation: Literal["receipt_image"]
    media_type: Literal["image/jpeg"]
    context: SubmissionContextInput


class DeviceWorkAcceptedResponse(APIModel):
    ok: Literal[True] = True
    print_intent_id: str
    print_job_id: str
    device_id: str
    state: Literal["accepted"] = "accepted"
    state_version: int
    accepted_at: datetime
    replayed: bool


__all__ = [
    "DeviceWorkAcceptedResponse",
    "PosPrintOriginInput",
    "ReceiptImageEnvelope",
    "SubmissionContextInput",
]
