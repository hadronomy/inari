from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    DriverExecutionResult,
    ExecutionClaim,
    ExecutionOwner,
    ExecutionReceipt,
    IoPermit,
    PreparedDeviceWork,
    RecoveryReport,
)


class ExecutionLedger(Protocol):
    def claim_next(
        self, owner: ExecutionOwner, *, device_id: str | None, now: datetime
    ) -> ExecutionClaim | None: ...

    def mark_prepared(self, claim: ExecutionClaim, *, now: datetime) -> None: ...

    def renew(self, claim: ExecutionClaim, *, now: datetime) -> ExecutionClaim: ...

    def mark_io_started(self, claim: ExecutionClaim, *, now: datetime) -> IoPermit: ...

    def note_permission_delivered(
        self, claim: ExecutionClaim, permit: IoPermit, *, now: datetime
    ) -> None: ...

    def finish(
        self,
        claim: ExecutionClaim,
        result: DriverExecutionResult,
        *,
        now: datetime,
    ) -> ExecutionReceipt: ...

    def fail_before_io(
        self,
        claim: ExecutionClaim,
        *,
        error_code: str,
        message_key: str,
        now: datetime,
    ) -> ExecutionReceipt: ...

    def abandon_after_marker(
        self, claim: ExecutionClaim, *, now: datetime
    ) -> ExecutionReceipt: ...

    def recover(self, owner: ExecutionOwner, *, now: datetime) -> RecoveryReport: ...


class ExecutionSpool(Protocol):
    def prepare(self, claim: ExecutionClaim) -> PreparedDeviceWork: ...

    def release(self, claim: ExecutionClaim, receipt: ExecutionReceipt) -> None: ...


class DeviceIoMarker(Protocol):
    @property
    def execution_id(self) -> str: ...

    @property
    def device_id(self) -> str: ...

    @property
    def marker_id(self) -> str: ...


class PreparedWorker(Protocol):
    async def wait_ready(self) -> None: ...

    async def execute(self, permit: DeviceIoMarker) -> DriverExecutionResult: ...

    async def close(self) -> None: ...


class DeviceWorker(Protocol):
    async def prepare(self, work: PreparedDeviceWork) -> PreparedWorker: ...


__all__ = ["DeviceWorker", "ExecutionLedger", "ExecutionSpool", "PreparedWorker"]
