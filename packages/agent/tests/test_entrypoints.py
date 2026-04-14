from __future__ import annotations

import runpy

from typer.testing import CliRunner

from iot_agent.config import AgentSettings


def test_package_entrypoint_invokes_main(mocker) -> None:
    mocked_main = mocker.patch("iot_agent.cli.main")

    runpy.run_module("iot_agent", run_name="__main__")

    mocked_main.assert_called_once_with()


def test_cli_serve_accepts_explicit_config_path(tmp_path, mocker) -> None:
    config_path = tmp_path / "iot-agent.toml"
    config_path.write_text(
        "[server]\nport = 8123\n",
        encoding="utf-8",
    )

    fake_container = type(
        "FakeContainer",
        (),
        {"tls_context_factory": None},
    )()

    mocked_build_container = mocker.patch("iot_agent.cli.build_container", return_value=fake_container)
    mocked_create_app = mocker.patch("iot_agent.cli.create_app", return_value="app")
    uvicorn_run = mocker.Mock()
    mocker.patch.dict("sys.modules", {"uvicorn": type("UvicornModule", (), {"run": uvicorn_run})()})

    from iot_agent.cli import app

    result = CliRunner().invoke(app, ["serve", "--config", str(config_path)])

    assert result.exit_code == 0, result.output
    called_settings = mocked_build_container.call_args.args[0]
    assert isinstance(called_settings, AgentSettings)
    assert called_settings.port == 8123
    mocked_create_app.assert_called_once()
    uvicorn_run.assert_called_once()
