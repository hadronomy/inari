from __future__ import annotations

from decimal import Decimal
from typing import Any


class EscPosRenderer:
    width = 42

    def render(self, receipt: dict[str, Any]) -> bytes:
        lines: list[bytes] = [b"\x1b@"]
        append = lines.append

        header = receipt.get("headerData", {})
        company = (header.get("company") or receipt.get("company", {}).get("name") or "Odoo POS").strip()
        append(self._center(company, emph=True))

        if order_date := header.get("date_order"):
            append(self._text(str(order_date)))
        if order_name := receipt.get("name"):
            append(self._text(f"Order: {order_name}"))

        append(self._rule())

        for line in receipt.get("orderlines", []):
            name = line.get("product_name") or line.get("product") or "Item"
            qty = line.get("qty", 1)
            price = line.get("price_display") or line.get("price_with_tax") or line.get("price") or 0
            append(self._wrap(f"{qty} x {name}"))
            append(self._right(self._money(price)))

        append(self._rule())
        append(self._kv("Tax", self._money(receipt.get("amount_tax", 0))))
        append(self._kv("Total", self._money(receipt.get("amount_total", 0)), emph=True))

        if receipt.get("amount_paid") is not None:
            append(self._kv("Paid", self._money(receipt.get("amount_paid", 0))))
        if receipt.get("amount_return"):
            append(self._kv("Change", self._money(receipt.get("amount_return", 0))))

        payments = receipt.get("paymentlines") or []
        if payments:
            append(self._rule())
            for payment in payments:
                append(self._kv(payment.get("name", "Payment"), self._money(payment.get("amount", 0))))

        if footer := receipt.get("footer"):
            append(self._rule())
            append(self._wrap(str(footer)))

        append(b"\n\n\n\x1dV\x41\x03")
        return b"".join(lines)

    def _money(self, value: Any) -> str:
        return f"{Decimal(str(value)):.2f}"

    def _rule(self) -> bytes:
        return ("-" * self.width + "\n").encode("utf-8")

    def _text(self, value: str) -> bytes:
        return (value[: self.width] + "\n").encode("utf-8", errors="replace")

    def _wrap(self, value: str) -> bytes:
        words = value.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) <= self.width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return ("\n".join(lines) + "\n").encode("utf-8", errors="replace")

    def _right(self, value: str) -> bytes:
        return (value.rjust(self.width) + "\n").encode("utf-8", errors="replace")

    def _center(self, value: str, *, emph: bool = False) -> bytes:
        prefix = b"\x1b!\x38" if emph else b""
        suffix = b"\x1b!\x00" if emph else b""
        return prefix + (value.center(self.width) + "\n").encode("utf-8", errors="replace") + suffix

    def _kv(self, key: str, value: str, *, emph: bool = False) -> bytes:
        left = str(key)[:20]
        right = str(value)
        spacing = max(1, self.width - len(left) - len(right))
        prefix = b"\x1b!\x08" if emph else b""
        suffix = b"\x1b!\x00" if emph else b""
        return prefix + f"{left}{' ' * spacing}{right}\n".encode("utf-8", errors="replace") + suffix
