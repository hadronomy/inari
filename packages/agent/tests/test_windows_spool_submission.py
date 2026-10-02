from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from inari.core.exceptions import PrinterServiceError
from inari.printing.drivers.windows import WindowsSpooler


@dataclass
class SpoolerApi:
    PRINTER_ENUM_CONNECTIONS: int = 4
    PRINTER_ENUM_LOCAL: int = 2
    job_id: int = 17
    written: int | None = None
    fail: str | None = None
    calls: list[str] = field(default_factory=list)

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail == name:
            raise OSError(f"{name} failed")

    def EnumPrinters(self, flags):
        return []

    def GetDefaultPrinter(self):
        return "test"

    def OpenPrinter(self, name):
        self._call("open")
        return 1

    def ClosePrinter(self, handle):
        self._call("close")

    def StartDocPrinter(self, handle, level, document):
        self._call("start_doc")
        return self.job_id

    def StartPagePrinter(self, handle):
        self._call("start_page")

    def WritePrinter(self, handle, payload):
        self._call("write")
        return len(payload) if self.written is None else self.written

    def EndPagePrinter(self, handle):
        self._call("end_page")

    def EndDocPrinter(self, handle):
        self._call("end_doc")

    def AbortPrinter(self, handle):
        self._call("abort")


def submit(api: SpoolerApi, *, pages: bool = True):
    return WindowsSpooler(api).write_job(
        printer_name="test",
        payload=b"^XA^FDtest^FS^XZ",
        data_type="RAW",
        document_name="Test",
        use_page_calls=pages,
    )


@pytest.mark.parametrize("pages", [True, False])
def test_complete_spool_job_returns_its_identity(pages):
    api = SpoolerApi()
    result = submit(api, pages=pages)
    assert result.job_id == 17
    assert result.bytes_written == len(b"^XA^FDtest^FS^XZ")
    assert api.calls == (
        ["open", "start_doc", "start_page", "write", "end_page", "end_doc", "close"]
        if pages
        else ["open", "start_doc", "write", "end_doc", "close"]
    )


@pytest.mark.parametrize("written", [0, 3, -1, 100, True])
def test_incomplete_write_aborts_instead_of_finishing_the_job(written):
    api = SpoolerApi(written=written)
    with pytest.raises(PrinterServiceError, match="complete document"):
        submit(api)
    assert api.calls == ["open", "start_doc", "start_page", "write", "abort", "close"]


@pytest.mark.parametrize("failure", ["start_page", "write", "end_page", "end_doc"])
def test_spool_failure_aborts_the_open_job(failure):
    api = SpoolerApi(fail=failure)
    with pytest.raises(PrinterServiceError, match=f"{failure} failed"):
        submit(api)
    assert api.calls[-2:] == ["abort", "close"]
    assert api.calls.count(failure) == 1
    if failure != "end_doc":
        assert "end_doc" not in api.calls


@pytest.mark.parametrize("job_id", [0, -1, True])
def test_invalid_job_identity_prevents_any_write(job_id):
    api = SpoolerApi(job_id=job_id)
    with pytest.raises(PrinterServiceError, match="valid job ID"):
        submit(api)
    assert api.calls == ["open", "start_doc", "close"]


def test_failed_abort_preserves_the_submission_error_and_closes_the_handle():
    api = SpoolerApi(written=0, fail="abort")
    with pytest.raises(PrinterServiceError, match="complete document"):
        submit(api)
    assert api.calls[-2:] == ["abort", "close"]
