from __future__ import annotations

import re
from dataclasses import dataclass


_OWNER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")


@dataclass(frozen=True, slots=True)
class SpoolOwner:
    """The process generation that owns recoverable spool reservations."""

    owner_id: str
    generation: int

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or not _OWNER_ID.fullmatch(self.owner_id):
            raise ValueError("Spool owner_id must be a safe identifier.")
        if (
            isinstance(self.generation, bool)
            or not isinstance(self.generation, int)
            or self.generation < 1
        ):
            raise ValueError("Spool owner generation must be positive.")
