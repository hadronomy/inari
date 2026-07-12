from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module_name",
    (
        "inari.gateway.connector",
        "inari.gateway.enrollment.service",
        "inari.security.certificates.lifecycle",
        "inari.local_api.app",
    ),
)
def test_agent_import_surfaces_are_order_independent(module_name: str) -> None:
    importlib.import_module(module_name)


@pytest.mark.parametrize(
    "args",
    (
        ("--help",),
        ("serve", "--help"),
    ),
)
def test_agent_cli_help_smoke(args: tuple[str, ...]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "inari", *args],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Usage:" in result.stdout
