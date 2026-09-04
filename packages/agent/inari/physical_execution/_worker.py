from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import multiprocessing
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from ..config import AgentSettings
from ..drivers import DriverRegistry
from ..printing.protocols import PrinterTransport
from ..printing.protocols.types import PrintJobResult, PrinterDevice
from ..print_jobs import OutputEvidence
from .models import (
    DriverExecutionResult,
    DriverOutcome,
    IoPermit,
    PreparedDeviceWork,
)


_READY_TIMEOUT_SECONDS = 10.0


class IsolatedPrinterWorker:
    """Run each printer submission in a fresh spawned process."""

    def __init__(self, settings: AgentSettings) -> None:
        self._settings = settings
        self._context = multiprocessing.get_context("spawn")

    async def prepare(self, work: PreparedDeviceWork) -> ProcessPreparedWorker:
        parent, child = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=_printer_process,
            args=(child, self._settings, work),
            name=f"inari-print-{work.device_id[:24]}",
            daemon=True,
        )
        process.start()
        child.close()
        return ProcessPreparedWorker(process=process, connection=parent, work=work)


@dataclass(slots=True)
class ProcessPreparedWorker:
    process: BaseProcess
    connection: Connection
    work: PreparedDeviceWork
    _closed: bool = False

    async def wait_ready(self) -> None:
        message = await asyncio.to_thread(
            _receive, self.connection, _READY_TIMEOUT_SECONDS
        )
        if message != ("ready", self.work.device_id):
            raise RuntimeError("The printer worker did not become ready.")

    async def execute(self, permit: IoPermit) -> DriverExecutionResult:
        self.connection.send(
            (
                "execute",
                permit.execution_id,
                permit.device_id,
                permit.marker_id,
            )
        )
        timeout = max(
            0.1,
            (self.work.deadline - datetime.now(tz=UTC)).total_seconds(),
        )
        try:
            message = await asyncio.to_thread(_receive, self.connection, timeout)
        except TimeoutError:
            self.process.terminate()
            return DriverExecutionResult(
                outcome=DriverOutcome.UNKNOWN,
                error_code="device_timeout",
                message_key="print.device_timeout",
            )
        if not isinstance(message, tuple) or not message:
            return DriverExecutionResult(
                outcome=DriverOutcome.UNKNOWN,
                error_code="worker_protocol_error",
                message_key="print.outcome_unknown",
            )
        if message[0] == "submitted":
            evidence = OutputEvidence(str(message[1]))
            return DriverExecutionResult(
                outcome=DriverOutcome.UNKNOWN,
                evidence=evidence,
                platform_job_id=message[2],
                error_code="output_not_confirmed",
                message_key="print.output_not_confirmed",
            )
        return DriverExecutionResult(
            outcome=DriverOutcome.UNKNOWN,
            error_code=str(message[1]) if len(message) > 1 else "device_failed",
            message_key="print.outcome_unknown",
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.process.is_alive():
                self.process.terminate()
            await asyncio.to_thread(self.process.join, 2.0)
        finally:
            self.connection.close()


def _receive(connection: Connection, timeout: float) -> Any:
    if not connection.poll(timeout):
        raise TimeoutError
    try:
        return connection.recv()
    except EOFError:
        return ("failed", "worker_exited")


def _printer_process(
    connection: Connection,
    settings: AgentSettings,
    work: PreparedDeviceWork,
) -> None:
    try:
        from ..di.drivers import build_printer_drivers

        registry = DriverRegistry(drivers=build_printer_drivers(settings))
        driver = next(
            driver
            for driver in registry.printer_drivers(available_only=True)
            if driver.metadata.key == work.driver_key
        )
        device = driver.get_device(work.device_name)
        if device.driver_key != work.driver_key:
            raise RuntimeError("The Device driver identity changed.")
        connection.send(("ready", work.device_id))
        command = connection.recv()
        if (
            not isinstance(command, tuple)
            or len(command) != 4
            or command[0] != "execute"
            or command[2] != work.device_id
        ):
            raise RuntimeError("The Device I/O permit is invalid.")
        result = _submit_prepared_work(driver, device, work)
        evidence = (
            OutputEvidence.SPOOLER
            if result.transport is not PrinterTransport.RAW
            or work.driver_key in {"cups.printers", "windows.printers"}
            else OutputEvidence.TRANSPORT
        )
        connection.send(
            (
                "submitted",
                evidence.value,
                str(result.job_id) if result.job_id is not None else None,
            )
        )
    except BaseException as error:
        try:
            code = getattr(error, "code", "device_failed")
            connection.send(("failed", str(code)))
        except BaseException:
            pass
    finally:
        connection.close()


def _submit_prepared_work(
    driver: Any, device: PrinterDevice, work: PreparedDeviceWork
) -> PrintJobResult:
    if work.media_type == "application/pdf":
        if work.driver_key not in {"cups.printers", "windows.printers"}:
            raise RuntimeError(
                "Report PDF output requires a platform document backend."
            )
        return driver.submit_document_job(
            device,
            work.content,
            media_type=work.media_type,
            document_name="Inari Report",
        )
    document_name = (
        "Inari Label"
        if work.media_type == "application/vnd.zebra-zpl"
        else "Inari Receipt"
    )
    return driver.submit_raw_job(
        device,
        work.content,
        document_name=document_name,
    )


__all__ = ["IsolatedPrinterWorker", "ProcessPreparedWorker"]
