from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from multiprocessing.connection import Connection as ProcessConnection
from multiprocessing.process import BaseProcess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from inari.config import AgentSettings
from inari.physical_execution._worker import ProcessPreparedWorker, _printer_process
from inari.physical_execution.models import DriverOutcome, IoPermit, PreparedDeviceWork
from inari.print_jobs import OutputEvidence
from inari.printing.drivers.windows import WindowsPrinterDriver, WindowsSpooler
from inari.runtime.models import DeviceRecord

from .test_windows_spool_submission import SpoolerApi


class ObservedSpoolerApi(SpoolerApi):
    def GetPrinter(self, handle, level):
        return {
            "pPrinterName": "test",
            "pPortName": "USB002",
            "pDriverName": "POS-80",
            "pDevMode": SimpleNamespace(Fields=0x8, PaperWidth=800),
            "Status": 0,
        }


@dataclass
class Connection:
    incoming: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    closed: bool = False

    def send(self, value):
        self.sent.append(value)

    def recv(self):
        if not self.incoming:
            raise EOFError
        return self.incoming.pop(0)

    def poll(self, timeout):
        return bool(self.incoming)

    def close(self):
        self.closed = True


@dataclass
class Process:
    terminated: bool = False

    def terminate(self):
        self.terminated = True

    def is_alive(self):
        return False

    def join(self, timeout):
        pass


@pytest.mark.anyio
@pytest.mark.parametrize("can_stop", [True, False])
async def test_worker_close_requires_a_stopped_process(can_stop):
    class StubbornProcess(Process):
        killed = False

        def is_alive(self):
            return not (self.killed and can_stop)

        def kill(self):
            self.killed = True

    process = StubbornProcess()
    connection = Connection()
    worker = ProcessPreparedWorker(
        Mock(spec=BaseProcess, wraps=process),
        Mock(spec=ProcessConnection, wraps=connection),
        work(),
    )
    if can_stop:
        await worker.close()
        assert worker._closed
    else:
        with pytest.raises(RuntimeError, match="did not stop"):
            await worker.close()
        assert not worker._closed
    assert process.terminated and process.killed and connection.closed


def work(*, operation="receipt_image", media_type="application/vnd.inari.escpos"):
    content = b"prepared-output"
    device = WindowsPrinterDriver(
        spooler=WindowsSpooler(Mock(wraps=ObservedSpoolerApi()))
    ).get_device("test")
    return PreparedDeviceWork(
        device_id=DeviceRecord.from_printer(device).id,
        driver_key="windows.printers",
        device_name="test",
        operation=operation,
        media_type=media_type,
        content=content,
        content_sha256=sha256(content).digest(),
        normalized_options=b"{}",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def permit():
    return IoPermit(
        attempt_id="attempt-1",
        lease_id="lease-1",
        execution_id="execution-1",
        job_id="job-1",
        device_id=work().device_id,
        marker_id="marker-1",
        marker_sequence=1,
        committed_at=datetime.now(UTC),
    )


def child_messages(monkeypatch, api, prepared):
    driver = WindowsPrinterDriver(
        spooler=WindowsSpooler(api), raw_name_hints=frozenset({"test"})
    )
    monkeypatch.setattr(
        "inari.di.drivers.build_printer_drivers", lambda settings: [driver]
    )
    connection = Connection(
        incoming=[("execute", "execution-1", prepared.device_id, "marker-1")]
    )
    _printer_process(connection, AgentSettings(), prepared)
    assert connection.closed
    return connection.sent


def test_worker_rejects_a_different_device_before_output(monkeypatch):
    api = ObservedSpoolerApi()
    messages = child_messages(
        monkeypatch, api, replace(work(), device_id="another-device")
    )
    assert messages[0][0] == "failed"
    assert "write" not in api.calls


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation,media_type",
    [
        ("receipt_image", "application/vnd.inari.escpos"),
        ("label_document", "application/vnd.zebra-zpl"),
    ],
)
async def test_complete_windows_raw_submission_confirms_spooler_evidence(
    monkeypatch, operation, media_type
):
    api = ObservedSpoolerApi()
    prepared = work(operation=operation, media_type=media_type)
    messages = child_messages(monkeypatch, api, prepared)
    worker = ProcessPreparedWorker(Process(), Connection(incoming=messages), prepared)
    await worker.wait_ready()

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.CONFIRMED
    assert result.evidence is OutputEvidence.SPOOLER
    assert result.platform_job_id == "17"
    assert result.error_code is None
    assert api.calls[-5:] == ["start_page", "write", "end_page", "end_doc", "close"]
    await worker.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "written,failure", [(0, None), (3, None), (None, "end_page"), (None, "end_doc")]
)
async def test_incomplete_windows_submission_cannot_confirm_output(
    monkeypatch, written, failure
):
    api = ObservedSpoolerApi(written=written, fail=failure)
    prepared = work()
    messages = child_messages(monkeypatch, api, prepared)
    worker = ProcessPreparedWorker(Process(), Connection(incoming=messages), prepared)
    await worker.wait_ready()

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.evidence is None
    assert result.error_code == "print_failed"
    assert api.calls[-2:] == ["abort", "close"]
    await worker.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        (),
        ("result",),
        ("result", "confirmed", "spooler"),
        ("result", "invalid", "spooler", "17"),
        ("result", "confirmed", "invalid", "17"),
        ("result", "confirmed", None, "17"),
        ("result", "confirmed", "spooler", 17),
        ("result", "confirmed", "spooler", "x" * 257),
        ("unexpected", "confirmed"),
    ],
)
async def test_invalid_worker_result_cannot_confirm_output(message):
    worker = ProcessPreparedWorker(Process(), Connection(incoming=[message]), work())

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.evidence is None
    assert result.error_code == "worker_protocol_error"


@pytest.mark.anyio
async def test_worker_timeout_terminates_the_process_without_confirmation():
    process = Process()
    worker = ProcessPreparedWorker(process, Connection(), work())

    result = await worker.execute(permit())

    assert process.terminated
    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.evidence is None
    assert result.error_code == "device_timeout"


@pytest.mark.anyio
async def test_submission_evidence_does_not_upgrade_an_unknown_outcome():
    worker = ProcessPreparedWorker(
        Process(),
        Connection(incoming=[("result", "unknown", "spooler", "17")]),
        work(),
    )

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.evidence is OutputEvidence.SPOOLER
    assert result.error_code == "output_not_confirmed"


@pytest.mark.anyio
@pytest.mark.parametrize("error", [EOFError, OSError])
async def test_worker_exit_cannot_confirm_output(error):
    class ExitedConnection(Connection):
        def poll(self, timeout):
            return True

        def recv(self):
            raise error

    worker = ProcessPreparedWorker(Process(), ExitedConnection(), work())

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.evidence is None
    assert result.error_code == "worker_exited"


@pytest.mark.anyio
@pytest.mark.parametrize("code", [None, "private/path", "x" * 65, ""])
async def test_worker_failure_uses_a_bounded_public_error_code(code):
    worker = ProcessPreparedWorker(
        Process(), Connection(incoming=[("failed", code)]), work()
    )

    result = await worker.execute(permit())

    assert result.outcome is DriverOutcome.UNKNOWN
    assert result.error_code == "device_failed"
