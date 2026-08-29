from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from .models import (
    EventStreamItem,
    JobEvent,
    PayloadFingerprint,
    PrintOrigin,
    PrintJob,
    PrintJobPage,
    PrintJobQuery,
    PrintJobScope,
    SubmissionResult,
)


class PrintJobSubmission(Protocol):
    """Content-free input needed to create one Print Job snapshot."""

    intent_id: str
    device_id: str
    origin: PrintOrigin
    managed_work_id: str | None
    expires_at: datetime
    contract_version: str


class PrintJobModule(Protocol):
    """Public Print Job facade. Scheduler details stay behind this boundary."""

    async def submit(
        self,
        work: PrintJobSubmission,
        *,
        scope: PrintJobScope,
        idempotency_key: str,
        fingerprint: PayloadFingerprint,
    ) -> SubmissionResult:
        """Admit work and return a new or exact replayed public snapshot."""

    async def get(self, job_id: str, *, scope: PrintJobScope) -> PrintJob | None:
        """Return one snapshot when it belongs to the requested scope."""

    async def query(self, query: PrintJobQuery) -> PrintJobPage:
        """Return bounded snapshots and an immutable reconciliation high-water mark."""

    async def subscribe(
        self, *, after: int, scope: PrintJobScope
    ) -> AsyncIterator[EventStreamItem]:
        """Yield a ready barrier, then durable events after a sequence cursor."""
        yield  # pragma: no cover - protocol method body


class PrintJobStore(Protocol):
    """Persistence port for the Print Job facade."""

    async def admit(
        self,
        work: PrintJobSubmission,
        *,
        scope: PrintJobScope,
        idempotency_key: str,
        fingerprint: PayloadFingerprint,
    ) -> SubmissionResult: ...

    async def get(self, job_id: str, *, scope: PrintJobScope) -> PrintJob | None: ...

    async def query(self, query: PrintJobQuery) -> PrintJobPage: ...

    async def events_after(
        self, sequence: int, *, scope: PrintJobScope
    ) -> AsyncIterator[JobEvent]: ...
