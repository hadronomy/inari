from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import sys
import tempfile
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

VERIFY_RUNTIME_OPTION = "--verify-runtime"


def verify_when_requested(
    load_application: Callable[[], object],
    *,
    arguments: Sequence[str] | None = None,
) -> bool:
    """Verify a frozen application's imports without starting its event loop."""
    requested_arguments = tuple(sys.argv[1:] if arguments is None else arguments)
    if not requested_arguments or requested_arguments[0] != VERIFY_RUNTIME_OPTION:
        return False
    if len(requested_arguments) != 2:
        raise SystemExit(f"{VERIFY_RUNTIME_OPTION} requires a report path")

    report = Path(requested_arguments[1])
    try:
        import ssl

        ssl.create_default_context()
        load_application()
        from inari.printing.receipt_pattern import receipt_image

        receipt_image()
        verify_printer_worker()
    except Exception:
        _write_report(report, traceback.format_exc())
        raise SystemExit(1) from None

    _write_report(
        report,
        "\n".join(
            (
                "Frozen runtime verified.",
                f"Python {sys.version.split()[0]}",
                ssl.OPENSSL_VERSION,
                "Isolated printer worker verified without Device I/O.",
            )
        ),
    )
    return True


def verify_printer_worker() -> None:
    """Exercise spawned printer IPC without sending a Device I/O permit."""
    asyncio.run(_verify_printer_worker())


async def _verify_printer_worker() -> None:
    from inari.config import AgentSettings
    from inari.di.drivers import build_printer_drivers
    from inari.physical_execution._worker import IsolatedPrinterWorker
    from inari.physical_execution.models import PreparedDeviceWork

    settings = AgentSettings()
    build_printer_drivers(settings)
    # A payload larger than the spawn pipe catches children that never read
    # their arguments. The absent Driver prevents Device discovery and I/O.
    content = b"inari-frozen-worker-verification\n" * 8192
    work = PreparedDeviceWork(
        device_id="frozen-runtime-verification",
        driver_key="inari.verification.absent-driver",
        device_name="frozen-runtime-verification",
        operation="receipt_image",
        media_type="application/vnd.inari.escpos",
        content=content,
        content_sha256=hashlib.sha256(content).digest(),
        normalized_options=b"{}",
        deadline=datetime.now(tz=UTC) + timedelta(seconds=10),
    )
    worker = await IsolatedPrinterWorker(settings).prepare(work)
    try:
        if not await asyncio.to_thread(worker.connection.poll, 10.0):
            raise RuntimeError("The frozen printer worker did not respond.")
        if worker.connection.recv() != ("failed", "device_failed"):
            raise RuntimeError("The frozen printer worker response is invalid.")
    finally:
        await worker.close()


def verify_migration_bundle() -> str:
    """Run every packaged migration against a temporary empty database."""
    from inari.db.migrations import DatabaseMigrator

    with tempfile.TemporaryDirectory(prefix="inari-frozen-runtime-") as directory:
        result = DatabaseMigrator(Path(directory) / "agent.sqlite3").ensure_current()
    return result.current_revision


def _write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
