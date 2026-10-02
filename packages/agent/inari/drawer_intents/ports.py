from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..client_trust import AuthorizedRequest
from ..device_authority import AdmissionPermit, CapabilityAdmissionTarget
from ..printing.protocols import PrintJobResult
from .models import (
    DrawerIntentRecord,
    DrawerIntentRequest,
    DrawerIntentState,
    DrawerLedgerAdmission,
)


class DrawerIntentLedger(Protocol):
    def find(
        self,
        *,
        intent_id: str,
        database: str,
        organization_id: str,
        site_id: str,
        pos_configuration_id: str,
        paired_client_id: str,
        now: datetime,
    ) -> DrawerIntentRecord | None: ...

    def admit(
        self,
        request: DrawerIntentRequest,
        *,
        authorization: AuthorizedRequest,
        fingerprint: bytes,
        now: datetime,
    ) -> DrawerLedgerAdmission: ...

    def rearm_before_io(
        self, record_id: str, *, now: datetime
    ) -> DrawerIntentRecord | None: ...

    def mark_io_started(
        self, record_id: str, *, now: datetime
    ) -> DrawerIntentRecord | None: ...

    def mark_succeeded(
        self,
        record_id: str,
        *,
        result: PrintJobResult,
        now: datetime,
    ) -> DrawerIntentRecord | None: ...

    def mark_terminal(
        self,
        record_id: str,
        *,
        expected_state: DrawerIntentState,
        state: DrawerIntentState,
        error_code: str,
        message_key: str,
        now: datetime,
    ) -> DrawerIntentRecord | None: ...

    def expire_stale_in_progress(self, *, cutoff: datetime, now: datetime) -> None: ...

    def query(
        self,
        *,
        intent_ids: tuple[str, ...],
        database: str,
        organization_id: str,
        site_id: str,
        pos_configuration_id: str,
        paired_client_id: str,
        now: datetime,
    ) -> tuple[DrawerIntentRecord, ...]: ...


class CashDrawerPort(Protocol):
    """The only physical seam owned by Drawer Intent execution."""

    def ensure_ready(self, device_id: str) -> None: ...

    def open_cash_drawer(self, device_id: str) -> PrintJobResult: ...


class DrawerAuthority(Protocol):
    def authorize(
        self,
        target: CapabilityAdmissionTarget,
        *,
        now: datetime | None = None,
    ) -> AdmissionPermit: ...

    def check(
        self,
        permit: AdmissionPermit,
        *,
        now: datetime | None = None,
    ) -> object: ...


__all__ = ["CashDrawerPort", "DrawerAuthority", "DrawerIntentLedger"]
