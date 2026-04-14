from __future__ import annotations

import runpy

from iot_agent.config import AgentSettings


def test_package_entrypoint_invokes_main(mocker) -> None:
    mocked_main = mocker.patch("iot_agent.main.main")

    runpy.run_module("iot_agent", run_name="__main__")

    mocked_main.assert_called_once_with()


def test_main_accepts_explicit_config_path(tmp_path, mocker) -> None:
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

    mocked_build_container = mocker.patch("iot_agent.main.build_container", return_value=fake_container)
    mocked_create_app = mocker.patch("iot_agent.main.create_app", return_value="app")
    mocker.patch.dict("sys.modules", {"uvicorn": type("UvicornModule", (), {"run": lambda *args, **kwargs: None})()})

    from iot_agent import main as agent_main

    agent_main.main(["--config", str(config_path)])

    called_settings = mocked_build_container.call_args.args[0]
    assert isinstance(called_settings, AgentSettings)
    assert called_settings.port == 8123
    mocked_create_app.assert_called_once()
