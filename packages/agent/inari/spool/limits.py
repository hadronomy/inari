from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from ..core.failures import ProblemCode, ProblemDetails
from ..db.schema import spool_reservations_table
from ..documents import DocumentKind
from .errors import SpoolAdmissionError
from .types import AdmissionManifest

MIB = 1024 * 1024
GIB = 1024 * MIB
RECEIPT_DERIVED_RESERVE = 16 * MIB
PERSISTENT_OVERHEAD = 64 * 1024
MINIMUM_VOLUME_BYTES = 4 * GIB
MINIMUM_FREE_BYTES = 1 * GIB
DEFAULT_DEVICE_QUEUE_LIMIT = 32
DEFAULT_AGENT_QUEUE_LIMIT = 256
DEFAULT_DEVICE_ORIGINAL_LIMIT = 64 * MIB
DEFAULT_AGENT_ORIGINAL_LIMIT = 512 * MIB
DEFAULT_DEVICE_PERSISTENT_LIMIT = 128 * MIB
DEFAULT_AGENT_PERSISTENT_LIMIT = 512 * MIB


@dataclass(frozen=True, slots=True)
class SpoolCapacityPolicy:
    device_queue_limit: int = DEFAULT_DEVICE_QUEUE_LIMIT
    agent_queue_limit: int = DEFAULT_AGENT_QUEUE_LIMIT
    device_original_limit: int = DEFAULT_DEVICE_ORIGINAL_LIMIT
    agent_original_limit: int = DEFAULT_AGENT_ORIGINAL_LIMIT
    device_persistent_limit: int = DEFAULT_DEVICE_PERSISTENT_LIMIT
    agent_persistent_limit: int = DEFAULT_AGENT_PERSISTENT_LIMIT

    def __post_init__(self) -> None:
        if any(
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
            for limit in (
                self.device_queue_limit,
                self.agent_queue_limit,
                self.device_original_limit,
                self.agent_original_limit,
                self.device_persistent_limit,
                self.agent_persistent_limit,
            )
        ):
            raise ValueError("Spool admission limits must be positive integers.")

    def persistent_bytes(self, manifest: AdmissionManifest) -> int:
        derived = (
            RECEIPT_DERIVED_RESERVE
            if manifest.operation == DocumentKind.RECEIPT_IMAGE.value
            else 0
        )
        return manifest.original_size_bytes + derived + PERSISTENT_OVERHEAD

    def assert_quota(
        self,
        connection: Connection,
        *,
        device_id: str,
        original_bytes: int,
        persistent_bytes: int,
    ) -> None:
        active = spool_reservations_table.c.state.in_(("held", "committed"))
        totals = (
            func.coalesce(func.sum(spool_reservations_table.c.queue_slots), 0),
            func.coalesce(func.sum(spool_reservations_table.c.original_bytes), 0),
            func.coalesce(func.sum(spool_reservations_table.c.persistent_bytes), 0),
        )
        agent_totals = connection.execute(select(*totals).where(active)).one()
        device_totals = connection.execute(
            select(*totals).where(
                active,
                spool_reservations_table.c.device_id == device_id,
            )
        ).one()
        if (
            device_totals[0] + 1 > self.device_queue_limit
            or agent_totals[0] + 1 > self.agent_queue_limit
        ):
            raise SpoolAdmissionError(
                ProblemCode.QUEUE_FULL,
                details=ProblemDetails(device_id=device_id),
            )
        if (
            device_totals[1] + original_bytes > self.device_original_limit
            or agent_totals[1] + original_bytes > self.agent_original_limit
            or device_totals[2] + persistent_bytes > self.device_persistent_limit
            or agent_totals[2] + persistent_bytes > self.agent_persistent_limit
        ):
            raise SpoolAdmissionError(
                ProblemCode.SPOOL_QUOTA_EXCEEDED,
                details=ProblemDetails(device_id=device_id),
            )

    def assert_volume_reserve(self, root: Path, *, required_bytes: int) -> None:
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            raise SpoolAdmissionError(ProblemCode.SPOOL_STORAGE_LOW) from None
        reserve = max(MINIMUM_FREE_BYTES, usage.total // 10)
        if usage.total < MINIMUM_VOLUME_BYTES or usage.free - required_bytes < reserve:
            raise SpoolAdmissionError(ProblemCode.SPOOL_STORAGE_LOW)


__all__ = [
    "DEFAULT_AGENT_ORIGINAL_LIMIT",
    "DEFAULT_AGENT_PERSISTENT_LIMIT",
    "DEFAULT_AGENT_QUEUE_LIMIT",
    "DEFAULT_DEVICE_ORIGINAL_LIMIT",
    "DEFAULT_DEVICE_PERSISTENT_LIMIT",
    "DEFAULT_DEVICE_QUEUE_LIMIT",
    "SpoolCapacityPolicy",
]
