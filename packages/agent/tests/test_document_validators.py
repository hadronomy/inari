from __future__ import annotations

import subprocess

import pytest

from inari.documents import DocumentAdmissionError
from inari.documents.validators import QpdfDocumentValidator, validate_label_document


def test_label_validator_accepts_a_bounded_zpl_format() -> None:
    validate_label_document(b"^XA^FO20,20^A0N,30,30^FDInari^FS^XZ")


@pytest.mark.parametrize(
    "content",
    [
        b"^XA~DGR:IMAGE.GRF,4,1,FFFF^XZ",
        b"^XA^DFR:FORMAT.ZPL^FDStored^FS^XZ",
        b"^XA^FDIncomplete^XZ",
        b"^XA^XZ",
    ],
)
def test_label_validator_rejects_unsafe_or_incomplete_zpl(content: bytes) -> None:
    with pytest.raises(DocumentAdmissionError):
        validate_label_document(content)


def test_pdf_validator_passes_content_to_qpdf_without_a_plaintext_file() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    QpdfDocumentValidator(runner=run).validate(b"%PDF-1.7\n%%EOF")

    command, arguments = calls[0]
    assert command == ["qpdf", "--check", "-"]
    assert arguments["input"] == b"%PDF-1.7\n%%EOF"
    assert "cwd" not in arguments


def test_pdf_validator_fails_closed_when_qpdf_rejects_content() -> None:
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 2, b"", b"invalid")

    with pytest.raises(DocumentAdmissionError, match="structural validation"):
        QpdfDocumentValidator(runner=run).validate(b"%PDF-1.7\ninvalid")
