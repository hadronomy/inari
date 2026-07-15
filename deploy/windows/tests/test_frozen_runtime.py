from __future__ import annotations

from pathlib import Path

import pytest

from deploy.windows.frozen_runtime import verify_when_requested


def test_normal_launch_does_not_run_bundle_verification(tmp_path: Path) -> None:
    report = tmp_path / "runtime.txt"
    application_loaded = False

    def load_application() -> object:
        nonlocal application_loaded
        application_loaded = True
        return object()

    verified = verify_when_requested(
        load_application,
        arguments=["inari://enroll"],
    )

    assert not verified
    assert not application_loaded
    assert not report.exists()


def test_bundle_verification_exercises_tls_and_application_imports(
    tmp_path: Path,
) -> None:
    report = tmp_path / "runtime.txt"
    application = object()

    verified = verify_when_requested(
        lambda: application,
        arguments=["--verify-runtime", str(report)],
    )

    assert verified
    contents = report.read_text(encoding="utf-8")
    assert contents.startswith("Frozen runtime verified.\nPython ")
    assert "OpenSSL" in contents


def test_bundle_verification_records_import_failure(tmp_path: Path) -> None:
    report = tmp_path / "runtime.txt"

    def fail_to_load() -> object:
        raise RuntimeError("application import failed")

    with pytest.raises(SystemExit) as raised:
        verify_when_requested(
            fail_to_load,
            arguments=["--verify-runtime", str(report)],
        )

    assert raised.value.code == 1
    contents = report.read_text(encoding="utf-8")
    assert "RuntimeError: application import failed" in contents
