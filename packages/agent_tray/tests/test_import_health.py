from __future__ import annotations

import importlib

import pytest


def test_tray_client_imports_without_agent_import_cycles() -> None:
    importlib.import_module("inari_tray.client")


def test_tray_cli_help_exits_before_starting_ui(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from inari_tray.main import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    assert "usage: inari-tray" in capsys.readouterr().out
