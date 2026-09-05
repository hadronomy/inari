from __future__ import annotations

import io
import math
from dataclasses import dataclass

import pikepdf

from ._pdf_fonts import PdfFontError, PdfFonts

MAX_INPUT_BYTES = 10 * 1024 * 1024
MAX_PAGES = 50
MAX_OBJECTS = 50_000
MAX_DEPTH = 64
MAX_STREAM_BYTES = 64 * 1024 * 1024
MAX_DECODED_BYTES = 256 * 1024 * 1024
MAX_PAGE_POINTS = 17 * 72
MAX_PAGE_PIXELS = 24_000_000
MAX_DOCUMENT_PIXELS = 200_000_000

_FORBIDDEN_KEYS = frozenset(
    {
        "/AA",
        "/OpenAction",
        "/JS",
        "/JavaScript",
        "/AcroForm",
        "/XFA",
        "/EmbeddedFiles",
        "/EF",
        "/AF",
        "/RichMediaContent",
        "/RichMediaSettings",
        "/3DD",
        "/Collection",
        "/Sound",
        "/Movie",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
        "/GoToE",
    }
)
_FORBIDDEN_TYPES = frozenset({"/Action", "/Annot", "/Filespec", "/EmbeddedFile"})
_FORBIDDEN_ACTIONS = frozenset(
    {
        "/JavaScript",
        "/Launch",
        "/SubmitForm",
        "/ImportData",
        "/GoToR",
        "/GoToE",
        "/URI",
        "/Rendition",
        "/Sound",
        "/Movie",
        "/SetOCGState",
        "/Trans",
    }
)


class PdfPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfPageGeometry:
    width_points: float
    height_points: float
    rotation: int
    width_pixels: int
    height_pixels: int


@dataclass(frozen=True, slots=True)
class PdfInspection:
    pages: tuple[PdfPageGeometry, ...]
    font_digests: tuple[str, ...]


def inspect_pdf(content: bytes, *, dpi: int) -> PdfInspection:
    """Inspect untrusted PDF bytes only inside a confined disposable process."""
    if type(dpi) is not int or dpi not in {150, 203, 300}:
        raise PdfPolicyError("The Report PDF resolution is outside Contract Major 1.")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_INPUT_BYTES:
        raise PdfPolicyError("The Report PDF exceeds its input limit.")
    if not content.startswith(b"%PDF-"):
        raise PdfPolicyError("The Report PDF header is invalid.")
    try:
        with pikepdf.Pdf.open(io.BytesIO(content), attempt_recovery=False) as document:
            if document.is_encrypted:
                raise PdfPolicyError("Encrypted Report PDFs are not permitted.")
            if not 0 < len(document.pages) <= MAX_PAGES:
                raise PdfPolicyError("The Report PDF exceeds its page limit.")
            if len(document.objects) > MAX_OBJECTS:
                raise PdfPolicyError("The Report PDF exceeds its object limit.")
            pages = tuple(_page_geometry(page, dpi) for page in document.pages)
            if (
                sum(page.width_pixels * page.height_pixels for page in pages)
                > MAX_DOCUMENT_PIXELS
            ):
                raise PdfPolicyError("The Report PDF exceeds its total pixel limit.")
            fonts = PdfFonts()
            _inspect_objects(document, fonts)
            for page in document.pages:
                fonts.check_page(page)
            if document.check_pdf_syntax() or document.get_warnings():
                raise PdfPolicyError("The Report PDF failed structural validation.")
            return PdfInspection(pages=pages, font_digests=fonts.digests)
    except (
        pikepdf.PdfError,
        PdfFontError,
        pikepdf.PasswordError,
        TypeError,
        ValueError,
        OverflowError,
    ) as error:
        if isinstance(error, PdfFontError):
            raise PdfPolicyError(str(error)) from None
        if isinstance(error, PdfPolicyError):
            raise
        # Parser diagnostics can contain document text, paths, or passwords.
        raise PdfPolicyError("The Report PDF failed structural validation.") from None


def _page_geometry(page: pikepdf.Page, dpi: int) -> PdfPageGeometry:
    media_box = _box(page.obj.get("/MediaBox"))
    crop_box = _box(page.obj.get("/CropBox", page.obj.get("/MediaBox")))
    if any(crop_box[index] < media_box[index] for index in (0, 1)) or any(
        crop_box[index] > media_box[index] for index in (2, 3)
    ):
        raise PdfPolicyError("The Report PDF CropBox extends outside its MediaBox.")
    user_unit = float(page.obj.get("/UserUnit", 1))
    rotation = page.obj.get("/Rotate", 0)
    if (
        not math.isfinite(user_unit)
        or user_unit <= 0
        or type(rotation) is not int
        or rotation % 90
    ):
        raise PdfPolicyError("The Report PDF page transformation is invalid.")
    width = (crop_box[2] - crop_box[0]) * user_unit
    height = (crop_box[3] - crop_box[1]) * user_unit
    if width > MAX_PAGE_POINTS or height > MAX_PAGE_POINTS:
        raise PdfPolicyError("The Report PDF exceeds its page geometry limit.")
    rotation %= 360
    if rotation in {90, 270}:
        width, height = height, width
    width_pixels, height_pixels = (
        math.ceil(width * dpi / 72),
        math.ceil(height * dpi / 72),
    )
    if width_pixels * height_pixels > MAX_PAGE_PIXELS:
        raise PdfPolicyError("The Report PDF exceeds its page pixel limit.")
    return PdfPageGeometry(width, height, rotation, width_pixels, height_pixels)


def _box(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, pikepdf.Array) or len(value) != 4:
        raise PdfPolicyError("The Report PDF page box is invalid.")
    x0, y0, x1, y1 = (float(item) for item in value)
    if (
        not all(math.isfinite(item) for item in (x0, y0, x1, y1))
        or x1 <= x0
        or y1 <= y0
    ):
        raise PdfPolicyError("The Report PDF page box is invalid.")
    return x0, y0, x1, y1


def _inspect_objects(document: pikepdf.Pdf, fonts: PdfFonts) -> None:
    seen: set[tuple[int, int]] = set()
    decoded_bytes = 0
    stack = [(value, 0) for value in document.objects]
    stack.append((document.trailer, 0))
    while stack:
        value, depth = stack.pop()
        if not isinstance(value, pikepdf.Object):
            continue
        if value.is_indirect:
            if value.objgen in seen:
                continue
            seen.add(value.objgen)
        if depth > MAX_DEPTH:
            raise PdfPolicyError("The Report PDF exceeds its object nesting limit.")
        if isinstance(value, pikepdf.Stream):
            if any(key in value for key in ("/F", "/FFilter", "/FDecodeParms")):
                raise PdfPolicyError("External Report PDF streams are not permitted.")
            if value.get("/Subtype") == pikepdf.Name.Image:
                _inspect_image(value)
            decoded = value.read_bytes(decode_level=pikepdf.StreamDecodeLevel.all)
            if len(decoded) > MAX_STREAM_BYTES:
                raise PdfPolicyError("The Report PDF exceeds its decoded stream limit.")
            decoded_bytes += len(decoded)
            if decoded_bytes > MAX_DECODED_BYTES:
                raise PdfPolicyError(
                    "The Report PDF exceeds its total decoded stream limit."
                )
            del decoded
        if isinstance(value, pikepdf.Dictionary | pikepdf.Stream):
            keys = set(value.keys())
            annotations = value.get("/Annots")
            if "/Annots" in keys and not (
                isinstance(annotations, pikepdf.Array) and len(annotations) == 0
            ):
                raise PdfPolicyError("The Report PDF contains prohibited annotations.")
            if (
                keys & _FORBIDDEN_KEYS
                or str(value.get("/Type", "")) in _FORBIDDEN_TYPES
            ):
                raise PdfPolicyError(
                    "The Report PDF contains prohibited active or attached content."
                )
            if str(value.get("/S", "")) in _FORBIDDEN_ACTIONS:
                raise PdfPolicyError("The Report PDF contains a prohibited action.")
            if value.get("/Type") == pikepdf.Name.Font:
                if str(value.get("/Subtype")) not in {"/CIDFontType0", "/CIDFontType2"}:
                    fonts.register(value)
            stack.extend((child, depth + 1) for _, child in value.items())
        elif isinstance(value, pikepdf.Array):
            stack.extend((child, depth + 1) for child in value)


def _inspect_image(value: pikepdf.Object) -> None:
    width, height = value.get("/Width"), value.get("/Height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise PdfPolicyError("The Report PDF image dimensions are invalid.")
    if width * height > MAX_PAGE_PIXELS:
        raise PdfPolicyError("The Report PDF image exceeds its pixel limit.")
