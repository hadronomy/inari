from __future__ import annotations

import hashlib
import io
import codecs
from decimal import Decimal
from dataclasses import dataclass

import pikepdf
from fontTools import agl
from fontTools.encodings.StandardEncoding import StandardEncoding
from fontTools.pens.basePen import NullPen
from fontTools.ttLib import TTFont, getTableClass

# Table modules load before the worker denies filesystem access.
for _tag in (
    "head",
    "hhea",
    "maxp",
    "cmap",
    "post",
    "hmtx",
    "loca",
    "glyf",
    "CFF ",
    "CFF2",
    "VORG",
    "vhea",
    "vmtx",
):
    getTableClass(_tag)

_SIMPLE_CODECS = {
    "/WinAnsiEncoding": codecs.lookup("cp1252"),
    "/MacRomanEncoding": codecs.lookup("mac_roman"),
}


class PdfFontError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _Font:
    digest: str
    glyphs: tuple[str, ...]
    encoding: tuple[str, ...] | None
    cid_to_gid: bytes | None

    def check_text(self, data: bytes) -> None:
        if self.encoding is not None:
            available = set(self.glyphs)
            if any(
                self.encoding[code] == ".notdef" or self.encoding[code] not in available
                for code in data
            ):
                raise PdfFontError("The Report PDF references a missing font glyph.")
            return
        if len(data) % 2:
            raise PdfFontError(
                "The Report PDF contains an incomplete font character code."
            )
        for offset in range(0, len(data), 2):
            character = int.from_bytes(data[offset : offset + 2], "big")
            if self.cid_to_gid is not None:
                index = character * 2
                if index + 2 > len(self.cid_to_gid):
                    raise PdfFontError(
                        "The Report PDF references a missing font glyph."
                    )
                character = int.from_bytes(self.cid_to_gid[index : index + 2], "big")
            if (
                character == 0
                or character >= len(self.glyphs)
                or self.glyphs[character] == ".notdef"
            ):
                raise PdfFontError("The Report PDF references a missing font glyph.")


class PdfFonts:
    def __init__(self) -> None:
        self._fonts: dict[tuple[int, int], _Font] = {}

    @property
    def digests(self) -> tuple[str, ...]:
        return tuple(sorted({font.digest for font in self._fonts.values()}))

    def register(self, font: pikepdf.Object) -> None:
        if not font.is_indirect:
            raise PdfFontError("The Report PDF font must have an indirect identity.")
        if font.objgen in self._fonts:
            return
        self._fonts[font.objgen] = _read_font(font)

    def check_page(self, page: pikepdf.Page) -> None:
        self._check_content(
            page, page.obj.get("/Resources", pikepdf.Dictionary()), frozenset(), None
        )

    def _check_content(self, content, resources, ancestors, current) -> None:
        if len(ancestors) > 64:
            raise PdfFontError("The Report PDF exceeds its Form XObject depth limit.")
        saved = []
        for operands, operator in pikepdf.parse_content_stream(content):
            operation = str(operator)
            if operation == "INLINE IMAGE":
                raise PdfFontError(
                    "Inline images are outside the Report PDF content policy."
                )
            if operation == "q":
                saved.append(current)
                if len(saved) > 64:
                    raise PdfFontError(
                        "The Report PDF exceeds its graphics state depth limit."
                    )
            elif operation == "Q":
                if not saved:
                    raise PdfFontError("The Report PDF graphics state is unbalanced.")
                current = saved.pop()
            elif operation == "Tf":
                if len(operands) != 2:
                    raise PdfFontError("The Report PDF font selection is invalid.")
                font = resources.get("/Font", pikepdf.Dictionary()).get(
                    str(operands[0])
                )
                if not isinstance(font, pikepdf.Dictionary):
                    raise PdfFontError("The Report PDF references a missing font.")
                self.register(font)
                current = self._fonts[font.objgen]
            elif operation == "gs":
                if len(operands) != 1:
                    raise PdfFontError(
                        "The Report PDF graphics state selection is invalid."
                    )
                state = resources.get("/ExtGState", pikepdf.Dictionary()).get(
                    str(operands[0])
                )
                if not isinstance(state, pikepdf.Dictionary):
                    raise PdfFontError(
                        "The Report PDF references a missing graphics state."
                    )
                if "/Font" in state:
                    selected = state["/Font"]
                    if (
                        not isinstance(selected, pikepdf.Array)
                        or len(selected) != 2
                        or not isinstance(selected[0], pikepdf.Dictionary)
                    ):
                        raise PdfFontError(
                            "The Report PDF graphics state font is invalid."
                        )
                    self.register(selected[0])
                    current = self._fonts[selected[0].objgen]
            elif operation in {"Tj", "TJ", "'", '"'}:
                if current is None or not operands:
                    raise PdfFontError(
                        "The Report PDF text has no selected embedded font."
                    )
                values = operands[-1] if operation == "TJ" else [operands[-1]]
                for value in values:
                    if isinstance(value, pikepdf.String):
                        current.check_text(bytes(value))
                    elif operation != "TJ" or not isinstance(
                        value, int | float | Decimal
                    ):
                        raise PdfFontError("The Report PDF text operand is invalid.")
            elif operation == "Do":
                if len(operands) != 1:
                    raise PdfFontError("The Report PDF XObject selection is invalid.")
                form = resources.get("/XObject", pikepdf.Dictionary()).get(
                    str(operands[0])
                )
                if not isinstance(form, pikepdf.Stream):
                    raise PdfFontError("The Report PDF references a missing XObject.")
                if form.get("/Subtype") == pikepdf.Name.Form:
                    if form.objgen in ancestors:
                        raise PdfFontError("The Report PDF has a cyclic Form XObject.")
                    self._check_content(
                        form,
                        form.get("/Resources", resources),
                        ancestors | {form.objgen},
                        current,
                    )
            elif (
                operation in {"scn", "SCN"}
                and operands
                and isinstance(operands[-1], pikepdf.Name)
            ):
                pattern = resources.get("/Pattern", pikepdf.Dictionary()).get(
                    str(operands[-1])
                )
                if not isinstance(pattern, pikepdf.Dictionary | pikepdf.Stream):
                    raise PdfFontError("The Report PDF references a missing pattern.")
                if pattern.get("/PatternType") == 1:
                    if (
                        not isinstance(pattern, pikepdf.Stream)
                        or pattern.objgen in ancestors
                    ):
                        raise PdfFontError(
                            "The Report PDF tiling pattern is invalid or cyclic."
                        )
                    self._check_content(
                        pattern,
                        pattern.get("/Resources", pikepdf.Dictionary()),
                        ancestors | {pattern.objgen},
                        current,
                    )
        if saved:
            raise PdfFontError("The Report PDF graphics state is unbalanced.")


def _read_font(font: pikepdf.Object) -> _Font:
    subtype = font.get("/Subtype")
    encoding = None
    mapping = None
    source = font
    if subtype == pikepdf.Name.Type0:
        children = font.get("/DescendantFonts")
        if font.get("/Encoding") not in {
            pikepdf.Name("/Identity-H"),
            pikepdf.Name("/Identity-V"),
        }:
            raise PdfFontError(
                "The Report PDF composite font requires an Identity CMap."
            )
        if (
            not isinstance(children, pikepdf.Array)
            or len(children) != 1
            or children[0].get("/Subtype") != pikepdf.Name.CIDFontType2
        ):
            raise PdfFontError(
                "The Report PDF composite font requires embedded TrueType glyphs."
            )
        source = children[0]
        cid_map = source.get("/CIDToGIDMap", pikepdf.Name.Identity)
        if isinstance(cid_map, pikepdf.Stream):
            mapping = cid_map.read_bytes()
            if len(mapping) > 131072 or len(mapping) % 2:
                raise PdfFontError("The Report PDF character-to-glyph map is invalid.")
        elif cid_map != pikepdf.Name.Identity:
            raise PdfFontError("The Report PDF character-to-glyph map is invalid.")
    elif subtype in {pikepdf.Name.TrueType, pikepdf.Name.Type1}:
        encoding = _simple_encoding(font.get("/Encoding"))
    else:
        raise PdfFontError("The Report PDF font type is outside the font policy.")
    descriptor = source.get("/FontDescriptor")
    if not isinstance(descriptor, pikepdf.Dictionary):
        raise PdfFontError("Every Report PDF font must be embedded.")
    programs = [
        descriptor[key]
        for key in ("/FontFile", "/FontFile2", "/FontFile3")
        if key in descriptor
    ]
    if len(programs) != 1 or not isinstance(programs[0], pikepdf.Stream):
        raise PdfFontError("Every Report PDF font must have one embedded program.")
    program = programs[0].read_bytes()
    if not 0 < len(program) <= 64 * 1024 * 1024:
        raise PdfFontError("The Report PDF embedded font exceeds its byte limit.")
    try:
        with TTFont(
            io.BytesIO(program), checkChecksums=2, recalcTimestamp=False
        ) as parsed:
            glyphs = tuple(parsed.getGlyphOrder())
            glyph_set = parsed.getGlyphSet()
            for name in glyphs:
                glyph_set[name].draw(NullPen())
    except Exception:
        raise PdfFontError(
            "The Report PDF embedded font program is invalid or unsupported."
        ) from None
    return _Font(hashlib.sha256(program).hexdigest(), glyphs, encoding, mapping)


def _simple_encoding(value: object) -> tuple[str, ...]:
    base = (
        value.get("/BaseEncoding", pikepdf.Name.StandardEncoding)
        if isinstance(value, pikepdf.Dictionary)
        else value
    )
    if base is None or base == pikepdf.Name.StandardEncoding:
        encoding = list(StandardEncoding)
    elif base in {pikepdf.Name.WinAnsiEncoding, pikepdf.Name.MacRomanEncoding}:
        codec = _SIMPLE_CODECS[str(base)]
        encoding = []
        for code in range(256):
            try:
                encoding.append(
                    agl.UV2AGL.get(ord(codec.decode(bytes([code]))[0]), ".notdef")
                )
            except UnicodeDecodeError:
                encoding.append(".notdef")
    else:
        raise PdfFontError("The Report PDF font encoding is unsupported.")
    if isinstance(value, pikepdf.Dictionary):
        index = None
        for item in value.get("/Differences", pikepdf.Array()):
            if type(item) is int:
                index = item
            elif (
                isinstance(item, pikepdf.Name)
                and index is not None
                and 0 <= index < 256
            ):
                encoding[index] = str(item)[1:]
                index += 1
            else:
                raise PdfFontError(
                    "The Report PDF font encoding differences are invalid."
                )
    return tuple(encoding)
