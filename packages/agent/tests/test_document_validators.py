from __future__ import annotations

import io
import subprocess
import sys

import pikepdf
import pytest
from inari_print_contracts import ZplLayout

from inari.documents import DocumentAdmissionError
from inari.documents._pdf_policy import PdfPolicyError, inspect_pdf
from inari.documents.validators import QpdfDocumentValidator, validate_label_document

LAYOUT = ZplLayout("test_4x6", 812, 1218)
LABEL = b"^XA^CI28^FO20,20^A0N,30,30^FH_^FDInari^FS^XZ"


def pdf_bytes(change=None, *, size=(612, 792), pages=1) -> bytes:
    with pikepdf.new() as document:
        for _ in range(pages):
            document.add_blank_page(page_size=size)
        if change:
            change(document)
        output = io.BytesIO()
        document.save(output)
        return output.getvalue()


def test_label_validator_accepts_one_bounded_zpl_envelope() -> None:
    validate_label_document(LABEL, layout=LAYOUT)


@pytest.mark.parametrize(
    "content",
    [
        b"^XA~DGR:IMAGE.GRF,4,1,FFFF^XZ",
        b"^XA^DFR:FORMAT.ZPL^FDStored^FS^XZ",
        b"^XA^FDIncomplete^XZ",
        b"^XA^XZ",
        LABEL + LABEL,
        LABEL.replace(b"^CI28", b""),
        LABEL.replace(b"^FH_", b""),
        LABEL.replace(b"^FO20,20", b"^FO800,20"),
        LABEL.replace(b"^XZ", b"^PQ2^XZ"),
    ],
)
def test_label_validator_rejects_unsafe_or_incomplete_zpl(content: bytes) -> None:
    with pytest.raises(DocumentAdmissionError):
        validate_label_document(content, layout=LAYOUT)


def test_pdf_validator_sends_source_to_the_disposable_worker() -> None:
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, b"", b"")

    content = pdf_bytes()
    QpdfDocumentValidator(runner=run).validate(content, dpi=203)
    command, arguments = calls[0]
    assert command == [
        sys.executable,
        "-I",
        "-B",
        "-m",
        "inari.documents._pdf_worker",
        "203",
    ]
    assert arguments["input"] == content
    assert arguments["timeout"] == 30


@pytest.mark.parametrize(
    "status,code",
    [
        (2, "document_policy_rejected"),
        (3, "service_unavailable"),
        (-9, "document_policy_rejected"),
    ],
)
def test_pdf_validator_fails_closed(status, code) -> None:
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, status, b"", b"private document text"
        )

    with pytest.raises(DocumentAdmissionError) as raised:
        QpdfDocumentValidator(runner=run).validate(pdf_bytes())
    assert raised.value.code == code
    assert "private document text" not in str(raised.value)


def test_pdf_policy_accepts_passive_content_and_applies_rotation_once() -> None:
    content = pdf_bytes(lambda pdf: pdf.pages[0].obj.__setitem__("/Rotate", 90))
    result = inspect_pdf(content, dpi=150)
    assert len(result.pages) == 1
    assert (result.pages[0].width_pixels, result.pages[0].height_pixels) == (1650, 1275)
    assert result.pages[0].rotation == 90


@pytest.mark.parametrize(
    "key",
    [
        "/OpenAction",
        "/AA",
        "/AcroForm",
        "/XFA",
        "/Annots",
        "/EmbeddedFiles",
        "/AF",
        "/RichMediaContent",
    ],
)
def test_pdf_policy_rejects_active_content_even_when_empty(key) -> None:
    content = pdf_bytes(lambda pdf: pdf.Root.__setitem__(key, pikepdf.Dictionary()))
    with pytest.raises(PdfPolicyError, match="prohibited"):
        inspect_pdf(content, dpi=203)


def test_pdf_policy_rejects_encryption_with_an_empty_user_password() -> None:
    with pikepdf.new() as document:
        document.add_blank_page()
        output = io.BytesIO()
        document.save(
            output, encryption=pikepdf.Encryption(owner="owner", user="", R=6)
        )
    with pytest.raises(PdfPolicyError, match="Encrypted"):
        inspect_pdf(output.getvalue(), dpi=203)


def test_pdf_policy_rejects_a_system_font() -> None:
    def add_font(pdf):
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
        pdf.pages[0].obj.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=font)
        )

    with pytest.raises(PdfPolicyError, match="embedded"):
        inspect_pdf(pdf_bytes(add_font), dpi=203)


def test_pdf_policy_checks_geometry_and_pixel_limits_independently() -> None:
    with pytest.raises(PdfPolicyError, match="geometry"):
        inspect_pdf(pdf_bytes(size=(18 * 72, 72)), dpi=150)
    with pytest.raises(PdfPolicyError, match="pixel"):
        inspect_pdf(pdf_bytes(size=(17 * 72, 17 * 72)), dpi=300)
    assert inspect_pdf(pdf_bytes(size=(17 * 72, 17 * 72)), dpi=203)


def test_pdf_policy_rejects_page_and_total_pixel_overflow() -> None:
    with pytest.raises(PdfPolicyError, match="page limit"):
        inspect_pdf(pdf_bytes(pages=51), dpi=150)
    with pytest.raises(PdfPolicyError, match="total pixel"):
        inspect_pdf(pdf_bytes(pages=25), dpi=300)


@pytest.mark.parametrize(
    "content", [b"%PDF-1.7\n%%EOF", b"%PDF-1.7\ninvalid", b"not a PDF"]
)
def test_pdf_policy_rejects_malformed_sources(content) -> None:
    with pytest.raises(PdfPolicyError):
        inspect_pdf(content, dpi=203)


def test_pdf_policy_rejects_invalid_crop_box() -> None:
    content = pdf_bytes(
        lambda pdf: pdf.pages[0].obj.__setitem__(
            "/CropBox", pikepdf.Array([0, 0, 700, 800])
        )
    )
    with pytest.raises(PdfPolicyError, match="CropBox"):
        inspect_pdf(content, dpi=203)


@pytest.mark.skipif(
    sys.platform != "linux", reason="The Linux worker requires seccomp and RLIMIT_AS."
)
def test_pdf_worker_checks_real_content_with_linux_isolation() -> None:
    QpdfDocumentValidator().validate(pdf_bytes())
    hostile = pdf_bytes(
        lambda pdf: pdf.Root.__setitem__(
            "/OpenAction", pikepdf.Dictionary(S=pikepdf.Name.JavaScript, JS="private")
        )
    )
    with pytest.raises(DocumentAdmissionError) as raised:
        QpdfDocumentValidator().validate(hostile)
    assert raised.value.code == "document_policy_rejected"


def embedded_font_pdf(text=b"A", *, broken_program=False, composite=False):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((500, 0))
    pen.lineTo((250, 700))
    pen.closePath()
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "A": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "A": (500, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "InariTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    program = io.BytesIO()
    builder.save(program)

    def add_font(pdf):
        stream = pdf.make_stream(b"invalid" if broken_program else program.getvalue())
        descriptor = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.FontDescriptor,
                FontName=pikepdf.Name.InariTest,
                Flags=32,
                FontBBox=pikepdf.Array([0, 0, 500, 700]),
                ItalicAngle=0,
                Ascent=800,
                Descent=-200,
                CapHeight=700,
                StemV=80,
                FontFile2=stream,
            )
        )
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.TrueType,
                BaseFont=pikepdf.Name.InariTest,
                Encoding=pikepdf.Name.WinAnsiEncoding,
                FirstChar=65,
                LastChar=65,
                Widths=pikepdf.Array([500]),
                FontDescriptor=descriptor,
            )
        )
        if composite:
            descendant = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.CIDFontType2,
                    BaseFont=pikepdf.Name.InariTest,
                    FontDescriptor=descriptor,
                    CIDToGIDMap=pikepdf.Name.Identity,
                    CIDSystemInfo=pikepdf.Dictionary(
                        Registry="Adobe", Ordering="Identity", Supplement=0
                    ),
                )
            )
            font = pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=pikepdf.Name.Font,
                    Subtype=pikepdf.Name.Type0,
                    BaseFont=pikepdf.Name.InariTest,
                    Encoding=pikepdf.Name("/Identity-H"),
                    DescendantFonts=pikepdf.Array([descendant]),
                )
            )
        pdf.pages[0].obj.Resources = pikepdf.Dictionary(
            Font=pikepdf.Dictionary(F1=font)
        )
        pdf.pages[0].obj.Contents = pdf.make_stream(
            b"BT /F1 12 Tf 10 10 Td <" + text.hex().encode() + b"> Tj ET"
        )

    return pdf_bytes(add_font)


def test_pdf_policy_records_a_checked_embedded_font_digest():
    result = inspect_pdf(embedded_font_pdf(), dpi=203)
    assert len(result.font_digests) == 1
    assert len(result.font_digests[0]) == 64


def test_pdf_policy_rejects_missing_glyphs_and_invalid_embedded_programs():
    with pytest.raises(PdfPolicyError, match="missing font glyph"):
        inspect_pdf(embedded_font_pdf(b"B"), dpi=203)
    with pytest.raises(PdfPolicyError, match="font program"):
        inspect_pdf(embedded_font_pdf(broken_program=True), dpi=203)


def test_pdf_policy_decodes_jpeg_images_within_the_stream_budget():
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (16, 16), "white").save(output, "JPEG")

    def add_image(pdf):
        stream = pdf.make_stream(
            output.getvalue(),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=16,
            Height=16,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        pdf.pages[0].obj.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Im1=stream)
        )
        pdf.pages[0].obj.Contents = pdf.make_stream(b"q 16 0 0 16 0 0 cm /Im1 Do Q")

    assert inspect_pdf(pdf_bytes(add_image), dpi=203)


@pytest.mark.skipif(
    sys.platform != "linux", reason="The Linux worker requires seccomp and RLIMIT_AS."
)
def test_linux_worker_checks_glyphs_without_system_font_access():
    QpdfDocumentValidator().validate(embedded_font_pdf(), dpi=203)
    with pytest.raises(DocumentAdmissionError):
        QpdfDocumentValidator().validate(embedded_font_pdf(b"B"), dpi=203)


def test_pdf_policy_checks_identity_cmap_glyph_ids():
    assert inspect_pdf(embedded_font_pdf(b"\x00\x01", composite=True), dpi=203)
    for text in (b"\x00\x00", b"\x00\x02", b"\x01"):
        with pytest.raises(PdfPolicyError):
            inspect_pdf(embedded_font_pdf(text, composite=True), dpi=203)


def test_pdf_policy_accepts_an_empty_annotation_array():
    content = pdf_bytes(
        lambda pdf: pdf.pages[0].obj.__setitem__("/Annots", pikepdf.Array())
    )
    assert inspect_pdf(content, dpi=203)


@pytest.mark.skipif(
    sys.platform != "linux", reason="The syscall policy is Linux-specific."
)
def test_linux_worker_cannot_open_files_create_sockets_or_start_processes():
    script = """
import errno
import socket
import subprocess
import sys
from inari.documents._pdf_worker import _confine_linux
_confine_linux()
for operation in (
    lambda: open('/dev/null', 'rb'),
    lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM),
    lambda: subprocess.run([sys.executable, '-c', 'pass']),
):
    try:
        operation()
    except OSError as error:
        assert error.errno == errno.EPERM
    else:
        raise AssertionError('The worker crossed its syscall policy.')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script], capture_output=True, timeout=10
    )
    assert result.returncode == 0, result.stderr.decode()
