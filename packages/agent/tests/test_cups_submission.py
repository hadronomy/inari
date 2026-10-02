from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from inari.core.exceptions import PrinterServiceError
from inari.drivers import DeviceIdentity, DeviceTransport
from inari.printing.drivers.cups import CupsPrinterDriver
from inari.printing.protocols import PrinterDevice


@dataclass
class CupsScheduler:
    fail_at: str | None = None
    raise_at: str | None = None
    attributes: dict = field(
        default_factory=lambda: {
            "document-format-supported": ["application/pdf"],
            "printer-is-accepting-jobs": True,
            "printer-state": 3,
            "printer-resolution-supported": [(203, 203, 3)],
        }
    )
    calls: list = field(default_factory=list)
    connections: int = 0

    def Connection(self):
        self.connections += 1
        scheduler = self
        connection_id = self.connections

        class Connection:
            def getPrinterAttributes(self, name):
                return scheduler.attributes

            def createJob(self, name, title, options):
                return self.response("create", 17, (name, title, options))

            def startDocument(self, *args):
                return self.response("start", 100, args)

            def writeRequestData(self, payload, length):
                assert len(payload) == length
                return self.response("write", 100, payload)

            def finishDocument(self, name):
                return self.response("finish", 0, name)

            def cancelJob(self, job_id):
                scheduler.calls.append(("cancel", connection_id, job_id))

            def response(self, step, success, arguments):
                scheduler.calls.append((step, connection_id, arguments))
                if scheduler.raise_at == step:
                    raise OSError("connection lost")
                return -1 if scheduler.fail_at == step else success

        return Connection()


def submit(scheduler: CupsScheduler, payload: bytes = b"checked-pdf"):
    driver = CupsPrinterDriver(cups_api=scheduler)
    printer = PrinterDevice(
        name="Office",
        driver_key="cups.printers",
        identity=DeviceIdentity(
            transport=DeviceTransport.SPOOLER, os_instance_id="office"
        ),
    )
    return driver.submit_document_job(
        printer,
        payload,
        media_type="application/pdf",
        document_name="Inari Report",
        dpi=203,
    )


def test_submission_preserves_resolution_and_bounds_each_write():
    scheduler = CupsScheduler()
    payload = b"p" * (128 * 1024 + 1)
    result = submit(scheduler, payload)

    assert result.job_id == 17
    assert scheduler.calls[0] == (
        "create",
        1,
        ("Office", "Inari Report", {"printer-resolution": "203dpi", "copies": "1"}),
    )
    chunks = [call[2] for call in scheduler.calls if call[0] == "write"]
    assert [len(chunk) for chunk in chunks] == [65536, 65536, 1]
    assert b"".join(chunks) == payload
    assert scheduler.connections == 1


@pytest.mark.parametrize("step", ["create", "start", "write", "finish"])
@pytest.mark.parametrize("response_kind", ["status", "exception"])
def test_failed_submission_stops_and_cancels_only_its_created_job(step, response_kind):
    scheduler = CupsScheduler(
        **{"fail_at" if response_kind == "status" else "raise_at": step}
    )
    with pytest.raises(PrinterServiceError):
        submit(scheduler)

    actions = [call[0] for call in scheduler.calls]
    expected = ["create", "start", "write", "finish"]
    expected = expected[: expected.index(step) + 1]
    if step != "create":
        expected.append("cancel")
        assert scheduler.calls[-1] == ("cancel", 2, 17)
    assert actions == expected


@pytest.mark.parametrize(
    "attributes",
    [
        {"document-format-supported": ["image/jpeg"]},
        {"printer-is-accepting-jobs": False},
        {"printer-is-accepting-jobs": None},
        {"printer-state": 5},
        {"printer-resolution-supported": [(300, 300, 3)]},
        {"printer-resolution-supported": [(203, 203, 4)]},
    ],
)
def test_preflight_rejects_queue_without_creating_a_job(attributes):
    scheduler = CupsScheduler()
    scheduler.attributes.update(attributes)
    with pytest.raises(PrinterServiceError):
        submit(scheduler)
    assert scheduler.calls == []
