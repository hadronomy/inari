from __future__ import annotations

import runpy

from typer.testing import CliRunner

from inari.config import AgentSettings


def test_package_entrypoint_invokes_main(mocker) -> None:
    mocked_main = mocker.patch("inari.cli.app")

    runpy.run_module("inari", run_name="__main__")

    mocked_main.assert_called_once_with(
        args=None,
        prog_name="inari",
        standalone_mode=False,
    )


def test_cli_serve_accepts_explicit_config_path(tmp_path, mocker) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        "[api]\nport = 8123\n",
        encoding="utf-8",
    )

    fake_container = type(
        "FakeContainer",
        (),
        {},
    )()

    mocked_build_container = mocker.patch(
        "inari.cli.build_container", return_value=fake_container
    )
    mocked_serve = mocker.patch("inari.cli.serve_agent")

    from inari.cli import app

    result = CliRunner().invoke(app, ["serve", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    called_settings = mocked_build_container.call_args.args[0]
    assert isinstance(called_settings, AgentSettings)
    assert called_settings.port == 8123
    mocked_serve.assert_called_once_with(called_settings, container=fake_container)
