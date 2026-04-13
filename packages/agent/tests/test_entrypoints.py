from __future__ import annotations

import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iot_agent.config import AgentSettings


class EntryPointTests(unittest.TestCase):
    def test_package_entrypoint_invokes_main(self) -> None:
        with patch("iot_agent.main.main") as mocked_main:
            runpy.run_module("iot_agent", run_name="__main__")

        mocked_main.assert_called_once_with()

    def test_main_accepts_explicit_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "iot-agent.toml"
            config_path.write_text(
                "[server]\nport = 8123\n",
                encoding="utf-8",
            )

            fake_container = type(
                "FakeContainer",
                (),
                {"tls_context_factory": None},
            )()

            with (
                patch("iot_agent.main.build_container", return_value=fake_container) as mocked_build_container,
                patch("iot_agent.main.create_app", return_value="app") as mocked_create_app,
                patch.dict("sys.modules", {"uvicorn": type("UvicornModule", (), {"run": lambda *args, **kwargs: None})()}),
            ):
                from iot_agent import main as agent_main

                agent_main.main(["--config", str(config_path)])

            called_settings = mocked_build_container.call_args.args[0]
            self.assertIsInstance(called_settings, AgentSettings)
            self.assertEqual(called_settings.port, 8123)
            mocked_create_app.assert_called_once()


if __name__ == "__main__":
    unittest.main()
