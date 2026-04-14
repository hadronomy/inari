from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from iot_agent.config import (
    AgentSettings,
    clear_settings_cache,
    generate_taplo_schema,
    load_settings,
    render_example_toml,
    write_generated_config_artifacts,
)
from iot_agent.config_paths import resolve_default_path_bundle


class AgentSettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_settings_cache()

    def test_load_settings_reads_nested_toml_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "iot-agent.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    config_version = 1

                    [server]
                    host = "0.0.0.0"
                    port = 8410
                    trusted_hosts = ["agent.local", "localhost"]

                    [logging]
                    level = "DEBUG"
                    directory = "./runtime/logs"

                    [paths]
                    profile = "production"
                    data_dir = "./runtime/data"
                    temp_dir = "./runtime/tmp"
                    runtime_database = "./runtime/data/agent.sqlite3"
                    security_state_dir = "./runtime/security"

                    [printing]
                    default_printer_name = "Kitchen Printer"
                    default_transport = "raw"
                    html_enabled = false

                    [gateway]
                    base_url = "https://controller.example.com"
                    auth_mode = "zitadel_service_account"
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path=config_path, environ={})

            self.assertEqual(settings.host, "0.0.0.0")
            self.assertEqual(settings.port, 8410)
            self.assertEqual(settings.path_profile, "production")
            self.assertEqual(settings.trusted_hosts, ["agent.local", "localhost"])
            self.assertEqual(settings.log_level, "DEBUG")
            self.assertEqual(settings.data_dir, (temp_path / "runtime/data").resolve())
            self.assertEqual(settings.log_dir, (temp_path / "runtime/logs").resolve())
            self.assertEqual(settings.temp_dir, (temp_path / "runtime/tmp").resolve())
            self.assertEqual(
                settings.runtime_database_path,
                (temp_path / "runtime/data/agent.sqlite3").resolve(),
            )
            self.assertEqual(settings.security_state_dir, (temp_path / "runtime/security").resolve())
            self.assertEqual(settings.default_printer_name, "Kitchen Printer")
            self.assertEqual(settings.default_printer_mode, "raw")
            self.assertFalse(settings.html_print_enabled)
            self.assertEqual(settings.upstream_base_url, "https://controller.example.com")
            self.assertEqual(settings.upstream_auth_mode.value, "zitadel_service_account")

    def test_load_settings_derives_runtime_paths_from_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "iot-agent.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [paths]
                    data_dir = "./state"
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path=config_path, environ={})

            self.assertEqual(settings.data_dir, (temp_path / "state").resolve())
            self.assertEqual(settings.runtime_database_path, (temp_path / "state/iot-agent.sqlite3").resolve())
            self.assertEqual(settings.security_state_dir, (temp_path / "state/security").resolve())

    def test_load_settings_reads_network_printers_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "iot-agent.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [printing]
                    default_transport = "auto"

                    [[printing.network_printers]]
                    name = "Kitchen LAN Printer"
                    host = "192.168.1.40"
                    port = 9100
                    is_default = true
                    preferred_transport = "raw"
                    cash_drawer = true
                    text_enabled = true

                    [[printing.network_printers]]
                    name = "Office Label Printer"
                    host = "192.168.1.41"
                    port = 9200
                    preferred_transport = "document"
                    document_enabled = true
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path=config_path, environ={})

            self.assertEqual(len(settings.network_printers), 2)
            self.assertEqual(settings.network_printers[0].name, "Kitchen LAN Printer")
            self.assertEqual(settings.network_printers[0].host, "192.168.1.40")
            self.assertTrue(settings.network_printers[0].is_default)
            self.assertTrue(settings.network_printers[0].text_enabled)
            self.assertEqual(settings.network_printers[1].preferred_transport, "document")
            self.assertTrue(settings.network_printers[1].document_enabled)

    def test_load_settings_reads_network_printers_from_env_json(self) -> None:
        settings = load_settings(
            environ={
                "IOT_AGENT_NETWORK_PRINTERS": json.dumps(
                    [
                        {
                            "name": "Back Bar Printer",
                            "host": "10.0.0.20",
                            "port": 9100,
                            "preferred_transport": "raw",
                            "text_enabled": True,
                        }
                    ]
                )
            }
        )

        self.assertEqual(len(settings.network_printers), 1)
        self.assertEqual(settings.network_printers[0].name, "Back Bar Printer")
        self.assertEqual(settings.network_printers[0].host, "10.0.0.20")
        self.assertTrue(settings.network_printers[0].text_enabled)

    def test_load_settings_merges_local_override_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "iot-agent.toml"
            local_path = temp_path / "iot-agent.local.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [logging]
                    level = "INFO"

                    [gateway.sync]
                    reconnect_delay_seconds = 5.0
                    """
                ).strip(),
                encoding="utf-8",
            )
            local_path.write_text(
                textwrap.dedent(
                    """
                    [logging]
                    level = "DEBUG"

                    [gateway.sync]
                    reconnect_delay_seconds = 2.5
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = load_settings(config_path=config_path, environ={})

            self.assertEqual(settings.log_level, "DEBUG")
            self.assertEqual(settings.gateway_reconnect_delay_seconds, 2.5)

    def test_load_settings_uses_env_as_final_override_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            config_path = temp_path / "iot-agent.toml"
            config_path.write_text(
                textwrap.dedent(
                    """
                    [logging]
                    level = "INFO"

                    [printing]
                    default_printer_name = "Kitchen Printer"
                    """
                ).strip(),
                encoding="utf-8",
            )

            settings = load_settings(
                config_path=config_path,
                environ={
                    "IOT_AGENT_LOG_LEVEL": "DEBUG",
                    "IOT_AGENT_DEFAULT_PRINTER_NAME": "Bar Printer",
                    "IOT_AGENT_TRUSTED_HOSTS": "[\"127.0.0.1\", \"localhost\"]",
                },
            )

            self.assertEqual(settings.log_level, "DEBUG")
            self.assertEqual(settings.default_printer_name, "Bar Printer")
            self.assertEqual(settings.trusted_hosts, ["127.0.0.1", "localhost"])

    def test_load_settings_uses_development_defaults_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "packages" / "agent").mkdir(parents=True)
            (temp_path / "pyproject.toml").write_text("[project]\nname='workspace'\n", encoding="utf-8")

            settings = load_settings(cwd=temp_path, environ={})

            self.assertEqual(settings.path_profile, "development")
            self.assertEqual(settings.data_dir, (temp_path / "data").resolve())
            self.assertEqual(settings.log_dir, (temp_path / "logs").resolve())
            self.assertEqual(settings.temp_dir, (temp_path / "tmp").resolve())
            self.assertEqual(settings.runtime_database_path, (temp_path / "data/iot-agent.sqlite3").resolve())
            self.assertEqual(settings.security_state_dir, (temp_path / "data/security").resolve())

    def test_load_settings_can_force_production_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            expected = resolve_default_path_bundle(profile="production", working_directory=temp_path)

            settings = load_settings(
                cwd=temp_path,
                environ={"IOT_AGENT_PATH_PROFILE": "production"},
            )

            self.assertEqual(settings.path_profile, "production")
            self.assertEqual(settings.data_dir, expected.data_dir)
            self.assertEqual(settings.log_dir, expected.log_dir)
            self.assertEqual(settings.temp_dir, expected.temp_dir)
            self.assertEqual(settings.runtime_database_path, expected.runtime_database_path)
            self.assertEqual(settings.security_state_dir, expected.security_state_dir)

    def test_generate_taplo_schema_is_draft4_compatible(self) -> None:
        schema = generate_taplo_schema()

        self.assertEqual(schema["$schema"], "http://json-schema.org/draft-04/schema#")
        self.assertIn("properties", schema)
        self.assertIn("server", schema["properties"])
        self.assertNotIn("$defs", json.dumps(schema))

    def test_render_example_toml_includes_schema_header_and_sections(self) -> None:
        rendered = render_example_toml()

        self.assertIn("#:schema ./schemas/iot-agent-config.schema.json", rendered)
        self.assertIn("[server]", rendered)
        self.assertIn("[paths]", rendered)
        self.assertIn("[gateway.sync]", rendered)
        self.assertIn("config_version = 1", rendered)
        self.assertIn('profile = "auto"', rendered)
        self.assertNotIn("[runtime]", rendered)
        self.assertNotIn("[security.tls]", rendered)
        self.assertNotIn("testserver", rendered)

    def test_write_generated_config_artifacts_writes_schema_and_example(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            schema_path = temp_path / "schemas" / "iot-agent-config.schema.json"
            example_path = temp_path / "config.example.toml"

            write_generated_config_artifacts(
                schema_output_path=schema_path,
                example_output_path=example_path,
            )

            self.assertTrue(schema_path.exists())
            self.assertTrue(example_path.exists())
            self.assertIn('"$schema": "http://json-schema.org/draft-04/schema#"', schema_path.read_text(encoding="utf-8"))
            self.assertIn("#:schema ./schemas/iot-agent-config.schema.json", example_path.read_text(encoding="utf-8"))

    def test_agent_settings_still_accept_flat_instantiation(self) -> None:
        settings = AgentSettings(
            log_level="DEBUG",
            default_printer_name="Kitchen Printer",
            gateway_mode="managed",
            gateway_exposure="loopback",
            path_profile="development",
            trusted_hosts='["127.0.0.1", "localhost"]',
        )

        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.default_printer_name, "Kitchen Printer")
        self.assertEqual(settings.gateway_mode.value, "managed")
        self.assertEqual(settings.gateway_exposure.value, "loopback")
        self.assertEqual(settings.path_profile, "development")
        self.assertEqual(settings.trusted_hosts, ["127.0.0.1", "localhost"])


if __name__ == "__main__":
    unittest.main()
