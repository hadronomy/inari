from __future__ import annotations

import sys
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
            )
        ),
    )
    return True


def _write_report(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")
