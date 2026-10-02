from __future__ import annotations

from datetime import UTC, datetime


class SystemTrustClock:
    """Read current UTC time for Client Trust decisions."""

    def now(self) -> datetime:
        return datetime.now(UTC)


__all__ = ["SystemTrustClock"]
