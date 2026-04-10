from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from textwrap import TextWrapper
from typing import Any, Iterable, Mapping

from ..printers import CutMode, EscPosCommands

TWOPLACES = Decimal("0.01")


@dataclass(slots=True, frozen=True)
class EscPosRendererConfig:
    line_width: int = 42
    encoding: str = "utf-8"
    trailing_feed_lines: int = 3
    cut_mode: CutMode | None = CutMode.PARTIAL


class EscPosRenderer:
    def __init__(self, config: EscPosRendererConfig | None = None) -> None:
        self.config = config or EscPosRendererConfig()
        self._wrapper = TextWrapper(
            width=self.config.line_width,
            break_long_words=True,
            break_on_hyphens=False,
        )

    @property
    def width(self) -> int:
        return self.config.line_width

    def render(self, receipt: Mapping[str, Any]) -> bytes:
        chunks: list[bytes] = [EscPosCommands.INITIALIZE]
        append = chunks.append

        header = self._mapping(receipt.get("headerData"))
        company = (
            (header.get("company") or self._mapping(receipt.get("company")).get("name") or "Receipt")
            .strip()
        )
        append(self._center(company, emphasized=True))

        if order_date := header.get("date_order"):
            append(self._text(str(order_date)))
        if order_name := receipt.get("name"):
            append(self._text(f"Order: {order_name}"))

        append(self._rule())

        for line in self._iter_mappings(receipt.get("orderlines")):
            name = line.get("product_name") or line.get("product") or "Item"
            quantity = line.get("qty", 1)
            price = line.get("price_display") or line.get("price_with_tax") or line.get("price") or 0
            append(self._wrap(f"{quantity} x {name}"))
            append(self._right(self._money(price)))

        append(self._rule())
        append(self._kv("Tax", self._money(receipt.get("amount_tax", 0))))
        append(self._kv("Total", self._money(receipt.get("amount_total", 0)), emphasized=True))

        if receipt.get("amount_paid") is not None:
            append(self._kv("Paid", self._money(receipt.get("amount_paid", 0))))
        if receipt.get("amount_return"):
            append(self._kv("Change", self._money(receipt.get("amount_return", 0))))

        payments = list(self._iter_mappings(receipt.get("paymentlines")))
        if payments:
            append(self._rule())
            for payment in payments:
                append(self._kv(str(payment.get("name", "Payment")), self._money(payment.get("amount", 0))))

        if footer := receipt.get("footer"):
            append(self._rule())
            append(self._wrap(str(footer)))

        if self.config.trailing_feed_lines:
            append(EscPosCommands.feed_lines(self.config.trailing_feed_lines))
        if self.config.cut_mode is not None:
            append(EscPosCommands.cut(self.config.cut_mode))

        return b"".join(chunks)

    def _money(self, value: Any) -> str:
        amount = Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
        return f"{amount:.2f}"

    def _rule(self) -> bytes:
        return ("-" * self.width + "\n").encode(self.config.encoding)

    def _text(self, value: str) -> bytes:
        return (value[: self.width] + "\n").encode(self.config.encoding, errors="replace")

    def _wrap(self, value: str) -> bytes:
        lines = self._wrapper.wrap(value.strip()) or [""]
        return ("\n".join(lines) + "\n").encode(self.config.encoding, errors="replace")

    def _right(self, value: str) -> bytes:
        return (value.rjust(self.width) + "\n").encode(self.config.encoding, errors="replace")

    def _center(self, value: str, *, emphasized: bool = False) -> bytes:
        prefix = b"\x1b!\x38" if emphasized else b""
        suffix = b"\x1b!\x00" if emphasized else b""
        centered = value[: self.width].center(self.width)
        return prefix + (centered + "\n").encode(self.config.encoding, errors="replace") + suffix

    def _kv(self, key: str, value: str, *, emphasized: bool = False) -> bytes:
        left = str(key)[:20]
        right = str(value)
        spacing = max(1, self.width - len(left) - len(right))
        prefix = b"\x1b!\x08" if emphasized else b""
        suffix = b"\x1b!\x00" if emphasized else b""
        line = f"{left}{' ' * spacing}{right}\n"
        return prefix + line.encode(self.config.encoding, errors="replace") + suffix

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _iter_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
        if not isinstance(value, list):
            return ()
        return (item for item in value if isinstance(item, Mapping))
