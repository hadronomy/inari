from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


class LabelContractError(ValueError):
    """The report cannot produce a Contract Major 1 Label Document."""


@dataclass(frozen=True, slots=True)
class ZplLayout:
    profile_id: str
    width_dots: int
    height_dots: int
    dpi: int = 203
    max_fields: int = 128
    max_field_bytes: int = 1024
    max_barcode_bytes: int = 256

    def __post_init__(self) -> None:
        if (
            not isinstance(self.profile_id, str)
            or not 1 <= len(self.profile_id) <= 128
            or type(self.dpi) is not int
            or self.dpi != 203
        ):
            raise LabelContractError("The ZPL layout requires a named 203 DPI profile.")
        for value, maximum in (
            (self.width_dots, 812),
            (self.height_dots, 1218),
            (self.max_fields, 128),
            (self.max_field_bytes, 1024),
            (self.max_barcode_bytes, 256),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise LabelContractError("The ZPL layout limit is invalid.")


_COMMAND = re.compile(r"\^([A-Z0-9]{2})")
_INTEGER = re.compile(r"[0-9]{1,4}\Z")
_HEX = frozenset("0123456789ABCDEF")
_SPACE = " \r\n\t"
_DATA_MATRIX_CODEWORDS = {
    10: 3,
    12: 5,
    14: 8,
    16: 12,
    18: 18,
    20: 22,
    22: 30,
    24: 36,
    26: 44,
    32: 62,
    36: 86,
    40: 114,
    44: 144,
    48: 174,
    52: 204,
    64: 280,
    72: 368,
    80: 456,
    88: 576,
    96: 696,
    104: 816,
    120: 1050,
    132: 1304,
    144: 1558,
}


def escape_field(value: str, *, data_matrix: bool = False) -> str:
    """Encode a business value for an immediately preceding ^FH_ command."""
    if not isinstance(value, str):
        raise LabelContractError("Label field data must be text.")
    _check_text(value)
    if data_matrix:
        value = value.replace("\\", "\\\\")
    return (
        "".join(
            f"_{byte:02X}" if byte in (0x5E, 0x7E, 0x5F) else chr(byte)
            for byte in value.encode("utf-8")
        )
        .encode("latin-1")
        .decode("utf-8")
    )


def _check_text(value: str) -> None:
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
        raise LabelContractError("Label field data contains a control character.")


def _field_data(value: str, *, data_matrix: bool) -> str:
    data = bytearray()
    offset = 0
    while offset < len(value):
        char = value[offset]
        if char == "_":
            token = value[offset + 1 : offset + 3]
            if len(token) != 2 or any(digit not in _HEX for digit in token):
                raise LabelContractError(
                    "Label field data has a noncanonical hex escape."
                )
            byte = int(token, 16)
            if byte not in (0x5E, 0x7E, 0x5F):
                raise LabelContractError("Only ZPL control bytes use hex escapes.")
            data.append(byte)
            offset += 3
        else:
            if char in "^~":
                raise LabelContractError(
                    "Label field data contains an unescaped ZPL prefix."
                )
            data.extend(char.encode("utf-8"))
            offset += 1
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LabelContractError("Label field data is not UTF-8.") from error
    _check_text(text)
    if data_matrix:
        offset = 0
        while offset < len(text):
            if text[offset] == "\\":
                if text[offset : offset + 2] != "\\\\":
                    raise LabelContractError(
                        "Data Matrix supports only doubled backslashes."
                    )
                offset += 1
            offset += 1
        text = text.replace("\\\\", "\\")
    return text


def _integer(value: str, maximum: int, *, minimum: int = 0) -> int:
    if not _INTEGER.fullmatch(value) or not minimum <= int(value) <= maximum:
        raise LabelContractError("A ZPL coordinate or dimension exceeds its layout.")
    return int(value)


@dataclass(slots=True)
class _Field:
    origin: tuple[str, int, int] | None = None
    kind: str | None = None
    height: int = 0
    width: int = 0
    module: int = 0
    columns: int = 0
    rows: int = 0
    hex_mode: bool = False

    def validate(self, source: str, layout: ZplLayout) -> None:
        if self.origin is None or self.kind is None or not self.hex_mode:
            raise LabelContractError(
                "Each label field needs an origin, type, and ^FH_."
            )
        text = _field_data(source, data_matrix=self.kind == "BX")
        limit = (
            layout.max_field_bytes if self.kind == "A0" else layout.max_barcode_bytes
        )
        if not text or len(text.encode("utf-8")) > limit:
            raise LabelContractError("Label field data exceeds its layout limit.")
        command, x, y = self.origin
        width, height = self.width, self.height
        if self.kind == "A0":
            width *= len(text)
        elif self.kind == "BC":
            if any(ord(char) > 127 or char == ">" for char in text):
                raise LabelContractError(
                    "Code 128 data cannot select a barcode control mode."
                )
            # Code set B gives a conservative bound when the printer selects set C.
            width = (11 * (len(text) + 2) + 13 + 20) * self.module
        elif self.kind == "BX":
            width = (self.columns + 2) * self.module
            height = (self.rows + 2) * self.module
            # Base 256 bounds arbitrary UTF-8 without depending on encoder compaction.
            data_bytes = len(text.encode("utf-8"))
            overhead = 2 if data_bytes <= 249 else 3
            if data_bytes + overhead > _DATA_MATRIX_CODEWORDS[self.columns]:
                raise LabelContractError(
                    "Data Matrix data exceeds its declared geometry."
                )
        top = y - height if command == "FT" else y
        if (
            top < 0
            or x + width > layout.width_dots
            or top + height > layout.height_dots
        ):
            raise LabelContractError("The label field does not fit its printable area.")


def split_labels(
    content: bytes, layout: ZplLayout, *, max_labels: int = 500
) -> tuple[bytes, ...]:
    """Validate a whole report before returning its exact, individual envelopes."""
    if not isinstance(content, bytes) or not 0 < len(content) <= 2 * 1024 * 1024:
        raise LabelContractError("A ZPL report must contain between 1 byte and 2 MiB.")
    if type(max_labels) is not int or not 1 <= max_labels <= 500:
        raise LabelContractError("The label count limit is invalid.")
    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LabelContractError("The ZPL report is not UTF-8.") from error
    labels: list[bytes] = []
    offset = 0
    start: int | None = None
    field = _Field()
    fields = 0
    utf8 = False
    module = 0
    commands = 0
    while offset < len(source):
        if source[offset] in _SPACE:
            offset += 1
            continue
        match = _COMMAND.match(source, offset)
        if not match:
            raise LabelContractError("The ZPL report contains data outside a command.")
        command = match[1]
        commands += 1
        if commands > 500 * 1024:
            raise LabelContractError("The ZPL report contains too many commands.")
        if command == "FD":
            end = source.find("^FS", match.end())
            if start is None or end < 0:
                raise LabelContractError("Label field data must end with ^FS.")
            field.validate(source[match.end() : end], layout)
            fields += 1
            if fields > layout.max_fields:
                raise LabelContractError("The label contains too many fields.")
            field = _Field()
            offset = end + 3
            continue
        end = source.find("^", match.end())
        if end < 0:
            end = len(source)
        arguments = source[match.end() : end].strip(_SPACE)
        if "~" in arguments or any(char in _SPACE for char in arguments):
            raise LabelContractError("The ZPL command parameters are invalid.")
        if command == "XA":
            if start is not None or arguments:
                raise LabelContractError("The ZPL report has an invalid ^XA boundary.")
            start, fields, utf8, module = offset, 0, False, 0
            field = _Field()
        elif start is None:
            raise LabelContractError("A ZPL command is outside a label envelope.")
        elif command == "XZ":
            if arguments or not utf8 or not fields or field != _Field():
                raise LabelContractError(
                    "The ZPL report has an incomplete label envelope."
                )
            labels.append(source[start : match.end()].encode("utf-8"))
            if len(labels) > max_labels:
                raise LabelContractError("The ZPL report contains too many labels.")
            start = None
        elif command == "CI":
            if utf8 or fields or field != _Field() or arguments != "28":
                raise LabelContractError(
                    "Each label requires one initial ^CI28 command."
                )
            utf8 = True
        elif not utf8:
            raise LabelContractError("The label requires ^CI28 before its fields.")
        elif command in {"FO", "FT"}:
            values = arguments.split(",")
            if field.origin is not None or len(values) != 2:
                raise LabelContractError("The label field origin is invalid.")
            field.origin = (
                command,
                _integer(values[0], layout.width_dots - 1),
                _integer(values[1], layout.height_dots - 1),
            )
        elif command == "BY":
            values = arguments.split(",")
            if field.hex_mode or not 1 <= len(values) <= 3:
                raise LabelContractError("The barcode defaults are invalid.")
            module = _integer(values[0], 10, minimum=1)
            if len(values) > 1:
                try:
                    ratio = Decimal(values[1])
                except InvalidOperation as error:
                    raise LabelContractError("The barcode ratio is invalid.") from error
                if not ratio.is_finite() or not Decimal(2) <= ratio <= Decimal(3):
                    raise LabelContractError("The barcode ratio is invalid.")
            if len(values) > 2:
                _integer(values[2], layout.height_dots, minimum=1)
        elif command in {"A0", "BC", "BX"}:
            _set_format(field, command, arguments, module, layout)
        elif command == "FH":
            if (
                field.kind is None
                or field.origin is None
                or field.hex_mode
                or arguments != "_"
            ):
                raise LabelContractError(
                    "The label requires ^FH_ immediately before ^FD."
                )
            if source[end : end + 3] != "^FD":
                raise LabelContractError(
                    "The label requires ^FH_ immediately before ^FD."
                )
            field.hex_mode = True
        else:
            raise LabelContractError(
                f"ZPL command ^{command} is outside Contract Major 1."
            )
        offset = end
    if start is not None or not labels:
        raise LabelContractError("The ZPL report has no complete label envelope.")
    return tuple(labels)


def _set_format(
    field: _Field, command: str, arguments: str, module: int, layout: ZplLayout
) -> None:
    values = arguments.split(",")
    if field.kind is not None or not values or values[0] != "N":
        raise LabelContractError("The label field format is invalid.")
    field.kind = command
    if command == "A0":
        if len(values) != 3:
            raise LabelContractError("^A0N requires explicit height and width.")
        field.height = _integer(values[1], layout.height_dots, minimum=1)
        field.width = _integer(values[2], layout.width_dots, minimum=1)
    elif command == "BC":
        if (
            not 5 <= len(values) <= 6
            or module == 0
            or any(flag != "N" for flag in values[2:])
        ):
            raise LabelContractError(
                "Code 128 requires explicit module size and no secondary text."
            )
        field.module = module
        field.height = _integer(values[1], layout.height_dots, minimum=1)
    else:
        if (
            len(values) != 7
            or values[2] != "200"
            or values[5] != "6"
            or values[6] != "\\"
        ):
            raise LabelContractError(
                "Data Matrix requires quality 200 and an explicit backslash escape."
            )
        field.module = _integer(values[1], 10, minimum=1)
        field.columns = _integer(values[3], 144, minimum=10)
        field.rows = _integer(values[4], 144, minimum=10)
        if field.columns != field.rows or field.columns not in _DATA_MATRIX_CODEWORDS:
            raise LabelContractError(
                "Data Matrix requires a supported square symbol size."
            )
