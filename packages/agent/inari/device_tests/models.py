from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from ..client_trust import AuthorizedRequest
from ..device_authority.testing import DeviceTestPermit
from ..printing.receipt_pattern import REQUIRED_CHECKS


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
DEVICE_TEST_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$"
_TEST_IDENTIFIER = re.compile(DEVICE_TEST_ID_PATTERN)


class PhysicalCheckAnswer(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    NOT_RUN = "not_run"


class TestState(StrEnum):
    __test__ = False

    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    AWAITING_CHECKS = "awaiting_checks"
    OUTCOME_UNKNOWN = "outcome_unknown"
    FAILED_ENVIRONMENT = "failed_environment"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class DeviceTestRequest:
    test_id: str
    device_id: str
    binding_revision_id: str

    def __post_init__(self) -> None:
        for name, pattern in (
            ("test_id", _TEST_IDENTIFIER),
            ("device_id", _IDENTIFIER),
            ("binding_revision_id", _IDENTIFIER),
        ):
            if not isinstance(getattr(self, name), str) or not pattern.fullmatch(
                getattr(self, name)
            ):
                raise ValueError(f"{name} must be a stable identifier")


@dataclass(frozen=True, slots=True)
class DeviceTestRecord:
    record_id: str
    test_id: str
    device_id: str
    binding_revision_id: str
    fingerprint: bytes
    state: TestState
    state_version: int
    accepted_at: datetime
    io_deadline: datetime
    started_at: datetime | None
    terminal_at: datetime | None
    output_evidence: str | None
    platform_job_id: str | None
    error_code: str | None
    graph: dict[str, object] | None
    checks: dict[str, str] | None
    signed_result: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class DeviceTestAccepted:
    record: DeviceTestRecord
    replayed: bool
    permit: DeviceTestPermit | None
    authorization: AuthorizedRequest


@dataclass(frozen=True, slots=True)
class TestWorkerClaim:
    __test__ = False

    record_id: str
    device_id: str
    claim_id: str


@dataclass(frozen=True, slots=True)
class TestIoMarker:
    __test__ = False

    execution_id: str
    device_id: str
    marker_id: str


def physical_checks(value: Mapping[str, str]) -> dict[str, str]:
    if set(value) != set(REQUIRED_CHECKS):
        raise ValueError("Supply an answer for each physical Device Test check.")
    return {name: PhysicalCheckAnswer(value[name]).value for name in REQUIRED_CHECKS}
