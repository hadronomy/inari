from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import logging

from .models import (
    DriverExecutionResult,
    DriverOutcome,
    ExecutionClaim,
    ExecutionOwner,
    ExecutionReceipt,
    PreIoFailure,
    RecoveryReport,
)
from .ports import DeviceWorker, ExecutionLedger, ExecutionSpool, PreparedWorker


logger = logging.getLogger(__name__)


class PhysicalExecution:
    """Own one physical Device Work execution from claim through recovery."""

    def __init__(
        self,
        *,
        ledger: ExecutionLedger,
        spool: ExecutionSpool,
        worker: DeviceWorker,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        heartbeat_seconds: float = 10.0,
    ) -> None:
        self._ledger = ledger
        self._spool = spool
        self._worker = worker
        self._clock = clock
        self._heartbeat_seconds = heartbeat_seconds

    async def run_one(
        self,
        owner: ExecutionOwner,
        *,
        device_id: str | None = None,
    ) -> ExecutionReceipt | None:
        claim = await asyncio.to_thread(
            self._ledger.claim_next,
            owner,
            device_id=device_id,
            now=self._now(),
        )
        if claim is None:
            return None

        heartbeat = asyncio.create_task(self._heartbeat(claim))
        prepared_worker: PreparedWorker | None = None
        marker_committed = False
        try:
            try:
                work = await asyncio.to_thread(self._spool.prepare, claim)
                await asyncio.to_thread(
                    self._ledger.mark_prepared, claim, now=self._now()
                )
                prepared_worker = await self._worker.prepare(work)
                await prepared_worker.wait_ready()
            except PreIoFailure as error:
                return await asyncio.to_thread(
                    self._ledger.fail_before_io,
                    claim,
                    error_code=error.error_code,
                    message_key=error.message_key,
                    now=self._now(),
                )
            except Exception:
                return await asyncio.to_thread(
                    self._ledger.fail_before_io,
                    claim,
                    error_code="service_unavailable",
                    message_key="print.service_unavailable",
                    now=self._now(),
                )

            try:
                permit = await asyncio.to_thread(
                    self._ledger.mark_io_started, claim, now=self._now()
                )
            except PreIoFailure as error:
                return await asyncio.to_thread(
                    self._ledger.fail_before_io,
                    claim,
                    error_code=error.error_code,
                    message_key=error.message_key,
                    now=self._now(),
                )
            marker_committed = True
            await asyncio.to_thread(
                self._ledger.note_permission_delivered,
                claim,
                permit,
                now=self._now(),
            )
            cancellation: asyncio.CancelledError | None = None
            try:
                result = await prepared_worker.execute(permit)
            except asyncio.CancelledError as error:
                cancellation = error
                result = DriverExecutionResult(
                    outcome=DriverOutcome.UNKNOWN,
                    error_code="worker_cancelled",
                    message_key="print.outcome_unknown",
                )
            except Exception:
                result = DriverExecutionResult(
                    outcome=DriverOutcome.UNKNOWN,
                    error_code="worker_exited",
                    message_key="print.outcome_unknown",
                )
            await prepared_worker.close()
            prepared_worker = None
            receipt = await asyncio.to_thread(
                self._ledger.finish, claim, result, now=self._now()
            )
            marker_committed = False
            try:
                await asyncio.to_thread(self._spool.release, claim, receipt)
            except Exception:
                logger.exception("Could not release Print Job artifacts.")
            if cancellation is not None:
                raise cancellation
            return receipt
        except BaseException:
            if prepared_worker is not None:
                await prepared_worker.close()
                prepared_worker = None
            if marker_committed:
                await asyncio.to_thread(
                    self._ledger.abandon_after_marker,
                    claim,
                    now=self._now(),
                )
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if prepared_worker is not None:
                await prepared_worker.close()

    async def recover_after_restart(
        self,
        owner: ExecutionOwner,
        *,
        now: datetime | None = None,
    ) -> RecoveryReport:
        return await asyncio.to_thread(
            self._ledger.recover,
            owner,
            now=now or self._now(),
        )

    async def _heartbeat(self, claim: ExecutionClaim) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            await asyncio.to_thread(self._ledger.renew, claim, now=self._now())

    def _now(self) -> datetime:
        return self._clock().astimezone(UTC)


__all__ = ["PhysicalExecution"]
