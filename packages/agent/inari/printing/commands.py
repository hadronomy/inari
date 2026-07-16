from __future__ import annotations

from dataclasses import MISSING, dataclass, fields
from enum import Enum, StrEnum
from typing import Any, ClassVar, Mapping, Self, TypeAlias, get_type_hints

from .protocols import CutMode, PrinterTransport


class DeviceCommandKind(StrEnum):
    OPEN_CASH_DRAWER = "open_cash_drawer"
    PRINT_TEST_PAGE = "print_test_page"
    FEED_LINES = "feed_lines"
    FEED_DOTS = "feed_dots"
    CUT_PAPER = "cut_paper"


@dataclass(frozen=True)
class DeviceCommand:
    kind: ClassVar[DeviceCommandKind]
    _registry: ClassVar[dict[DeviceCommandKind, type[DeviceCommand]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        kind = getattr(cls, "kind", None)
        if isinstance(kind, DeviceCommandKind):
            DeviceCommand._registry[kind] = cls

    def to_payload(self) -> dict[str, Any]:
        payload = {"kind": self.kind.value}
        for field in fields(type(self)):
            payload[field.name] = _encode_value(getattr(self, field.name))
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> DeviceCommand:
        raw_kind = payload.get("kind")
        if raw_kind is None:
            raise ValueError("Device command payload is missing the 'kind' field.")
        kind = DeviceCommandKind(str(raw_kind))
        concrete_type = cls._registry.get(kind)
        if concrete_type is None:
            raise ValueError(f"Unsupported device command kind: {kind!r}")
        return concrete_type._from_payload(payload)

    @classmethod
    def _from_payload(cls, payload: Mapping[str, Any]) -> Self:
        kwargs: dict[str, Any] = {}
        hints = get_type_hints(cls)
        for field in fields(cls):
            if field.name in payload:
                raw_value = payload[field.name]
                kwargs[field.name] = _decode_value(
                    hints.get(field.name, Any), raw_value
                )
                continue
            if field.default is not MISSING or field.default_factory is not MISSING:
                continue
            raise ValueError(
                f"Device command payload is missing the '{field.name}' field."
            )
        return cls(**kwargs)


@dataclass(slots=True, frozen=True)
class OpenCashDrawer(DeviceCommand):
    kind: ClassVar[DeviceCommandKind] = DeviceCommandKind.OPEN_CASH_DRAWER


@dataclass(slots=True, frozen=True)
class PrintTestPage(DeviceCommand):
    transport: PrinterTransport = PrinterTransport.AUTO
    kind: ClassVar[DeviceCommandKind] = DeviceCommandKind.PRINT_TEST_PAGE


@dataclass(slots=True, frozen=True)
class FeedLines(DeviceCommand):
    count: int
    kind: ClassVar[DeviceCommandKind] = DeviceCommandKind.FEED_LINES

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("Feed line count must be greater than zero.")


@dataclass(slots=True, frozen=True)
class FeedDots(DeviceCommand):
    count: int
    kind: ClassVar[DeviceCommandKind] = DeviceCommandKind.FEED_DOTS

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("Feed dot count must be greater than zero.")


@dataclass(slots=True, frozen=True)
class CutPaper(DeviceCommand):
    mode: CutMode = CutMode.PARTIAL
    kind: ClassVar[DeviceCommandKind] = DeviceCommandKind.CUT_PAPER


AnyDeviceCommand: TypeAlias = (
    OpenCashDrawer | PrintTestPage | FeedLines | FeedDots | CutPaper
)


def _encode_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _decode_value(annotation: Any, value: Any) -> Any:
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if annotation is int:
        return int(value)
    return value
