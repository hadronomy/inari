from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from inari.config import (
    AgentSettings,
    generate_taplo_schema,
    load_settings,
    render_example_toml,
    write_generated_config_artifacts,
)
from inari.config_paths import resolve_default_path_bundle
from inari.security.models import GatewayExposure, GatewayMode


def test_load_settings_reads_new_nested_toml_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            config_version = 1

            [agent]
            mode = "managed"

            [api]
            host = "0.0.0.0"
            port = 8410
            allowed_hosts = ["agent.local", "localhost"]
            exposure = "lan"

            [logging]
            level = "DEBUG"
            directory = "./runtime/logs"

            [storage]
            profile = "production"
            data_dir = "./runtime/data"
            temp_dir = "./runtime/tmp"
            database_path = "./runtime/data/agent.sqlite3"
            security_state_dir = "./runtime/security"

            [devices.printing]
            default_printer = "Kitchen Printer"
            default_transport = "raw"
            enable_html = false

            [controller]
            base_url = "https://controller.example.com"
            auth_provider = "zitadel_service_account"
            mtls_mode = "required"

            [controller.sync]
            status_interval = "45s"

            [controller.reconnect]
            initial_delay = "2500ms"
            """
        ).strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, environ={})

    assert settings.host == "0.0.0.0"
    assert settings.port == 8410
    assert settings.path_profile == "production"
    assert settings.gateway_mode.value == "managed"
    assert settings.gateway_exposure.value == "lan"
    assert settings.trusted_hosts == ["agent.local", "localhost"]
    assert settings.log_level == "DEBUG"
    assert settings.data_dir == (tmp_path / "runtime/data").resolve()
    assert settings.log_dir == (tmp_path / "runtime/logs").resolve()
    assert settings.temp_dir == (tmp_path / "runtime/tmp").resolve()
    assert (
        settings.runtime_database_path
        == (tmp_path / "runtime/data/agent.sqlite3").resolve()
    )
    assert settings.security_state_dir == (tmp_path / "runtime/security").resolve()
    assert settings.default_printer_name == "Kitchen Printer"
    assert settings.default_printer_mode == "raw"
    assert settings.html_print_enabled is False
    assert settings.upstream_base_url == "https://controller.example.com"
    assert settings.upstream_auth_mode.value == "zitadel_service_account"
    assert settings.upstream_mutual_tls_mode.value == "required"
    assert settings.gateway_sync_interval_seconds == 45.0
    assert settings.gateway_reconnect_delay_seconds == 2.5


def test_load_settings_rejects_legacy_gateway_shaped_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [server]
            port = 8410

            [gateway]
            base_url = "https://controller.example.com"
            """
        ).strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(config_path=config_path, environ={})


def test_load_settings_derives_runtime_paths_from_data_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [storage]
            data_dir = "./state"
            """
        ).strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, environ={})

    assert settings.data_dir == (tmp_path / "state").resolve()
    assert (
        settings.runtime_database_path == (tmp_path / "state/inari.sqlite3").resolve()
    )
    assert settings.security_state_dir == (tmp_path / "state/security").resolve()


def test_load_settings_reads_network_printers_from_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [devices.printing]
            default_transport = "auto"

            [[devices.printing.printers]]
            name = "Kitchen LAN Printer"
            host = "192.168.1.40"
            port = 9100
            default = true
            transport = "raw"
            cash_drawer = true
            text_enabled = true

            [[devices.printing.printers]]
            name = "Office Label Printer"
            host = "192.168.1.41"
            port = 9200
            transport = "document"
            document_enabled = true
            """
        ).strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, environ={})

    assert len(settings.network_printers) == 2
    assert settings.network_printers[0].name == "Kitchen LAN Printer"
    assert settings.network_printers[0].host == "192.168.1.40"
    assert settings.network_printers[0].is_default is True
    assert settings.network_printers[0].text_enabled is True
    assert settings.network_printers[1].preferred_transport == "document"
    assert settings.network_printers[1].document_enabled is True


def test_load_settings_reads_network_printers_from_env_json() -> None:
    settings = load_settings(
        environ={
            "INARI_NETWORK_PRINTERS": json.dumps(
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

    assert len(settings.network_printers) == 1
    assert settings.network_printers[0].name == "Back Bar Printer"
    assert settings.network_printers[0].host == "10.0.0.20"
    assert settings.network_printers[0].text_enabled is True


def test_default_gateway_mutual_tls_mode_is_optional() -> None:
    settings = load_settings(environ={})

    assert settings.upstream_mutual_tls_mode.value == "optional"


def test_load_settings_merges_local_override_file(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    local_path = tmp_path / "inari.local.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [logging]
            level = "INFO"

            [controller.reconnect]
            initial_delay = "5s"
            """
        ).strip(),
        encoding="utf-8",
    )
    local_path.write_text(
        textwrap.dedent(
            """
            [logging]
            level = "DEBUG"

            [controller.reconnect]
            initial_delay = "2500ms"
            """
        ).strip(),
        encoding="utf-8",
    )

    settings = load_settings(config_path=config_path, environ={})

    assert settings.log_level == "DEBUG"
    assert settings.gateway_reconnect_delay_seconds == 2.5


def test_load_settings_uses_env_as_final_override_layer(tmp_path: Path) -> None:
    config_path = tmp_path / "inari.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [logging]
            level = "INFO"

            [devices.printing]
            default_printer = "Kitchen Printer"
            """
        ).strip(),
        encoding="utf-8",
    )

    settings = load_settings(
        config_path=config_path,
        environ={
            "INARI_LOG_LEVEL": "DEBUG",
            "INARI_DEFAULT_PRINTER_NAME": "Bar Printer",
            "INARI_TRUSTED_HOSTS": '["127.0.0.1", "localhost"]',
        },
    )

    assert settings.log_level == "DEBUG"
    assert settings.default_printer_name == "Bar Printer"
    assert settings.trusted_hosts == ["127.0.0.1", "localhost"]


def test_load_settings_uses_development_defaults_inside_workspace(
    tmp_path: Path,
) -> None:
    (tmp_path / "packages" / "agent").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='workspace'\n", encoding="utf-8"
    )
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    settings = load_settings(cwd=tmp_path, environ={})

    assert settings.path_profile == "development"
    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.log_dir == (tmp_path / "logs").resolve()
    assert settings.temp_dir == (tmp_path / "tmp").resolve()
    assert settings.runtime_database_path == (tmp_path / "data/inari.sqlite3").resolve()
    assert settings.security_state_dir == (tmp_path / "data/security").resolve()


def test_load_settings_can_force_production_defaults(tmp_path: Path) -> None:
    expected = resolve_default_path_bundle(
        profile="production", working_directory=tmp_path
    )

    settings = load_settings(
        cwd=tmp_path,
        environ={"INARI_PATH_PROFILE": "production"},
    )

    assert settings.path_profile == "production"
    assert settings.data_dir == expected.data_dir
    assert settings.log_dir == expected.log_dir
    assert settings.temp_dir == expected.temp_dir
    assert settings.runtime_database_path == expected.runtime_database_path
    assert settings.security_state_dir == expected.security_state_dir


def test_generate_taplo_schema_is_draft4_compatible() -> None:
    schema = generate_taplo_schema()

    assert schema["$schema"] == "http://json-schema.org/draft-04/schema#"
    assert "properties" in schema
    assert "agent" in schema["properties"]
    assert "api" in schema["properties"]
    assert "$defs" not in json.dumps(schema)


def test_render_example_toml_includes_schema_header_and_sections() -> None:
    rendered = render_example_toml()

    assert "#:schema ./schemas/inari-config.schema.json" in rendered
    assert "[agent]" in rendered
    assert "[api]" in rendered
    assert "[api.cors]" in rendered
    assert "[storage]" in rendered
    assert "[devices.printing]" in rendered
    assert "[runtime.jobs.retry]" in rendered
    assert "[auth.local]" in rendered
    assert "[controller.queue]" in rendered
    assert "[transport.zenoh]" in rendered
    assert "[certificates.step_ca]" in rendered
    assert "[server]" not in rendered
    assert "[paths]" not in rendered
    assert "[security]" not in rendered
    assert "[gateway]" not in rendered
    assert "\nconfig_version = 1\n" in rendered
    assert '# profile = "production"' in rendered
    assert "network_printers = []" not in rendered
    assert "# [[devices.printing.printers]]" in rendered
    assert "Uncomment only the settings you want to override." in rendered
    assert "testserver" not in rendered
    assert '# enrollment_token = "enrollment-token"' in rendered
    assert "bootstrap_token" not in rendered
    assert "enrollment_code" not in rendered


def test_write_generated_config_artifacts_writes_schema_and_example(
    tmp_path: Path,
) -> None:
    schema_path = tmp_path / "schemas" / "inari-config.schema.json"
    example_path = tmp_path / "config.example.toml"

    write_generated_config_artifacts(
        schema_output_path=schema_path,
        example_output_path=example_path,
    )

    assert schema_path.exists()
    assert example_path.exists()
    assert (
        '"$schema": "http://json-schema.org/draft-04/schema#"'
        in schema_path.read_text(encoding="utf-8")
    )
    assert "#:schema ./schemas/inari-config.schema.json" in example_path.read_text(
        encoding="utf-8"
    )


def test_agent_settings_still_accept_flat_instantiation() -> None:
    settings = AgentSettings(
        log_level="DEBUG",
        default_printer_name="Kitchen Printer",
        gateway_mode=GatewayMode.MANAGED,
        gateway_exposure=GatewayExposure.LOOPBACK,
        path_profile="development",
        trusted_hosts=["127.0.0.1", "localhost"],
    )

    assert settings.log_level == "DEBUG"
    assert settings.default_printer_name == "Kitchen Printer"
    assert settings.gateway_mode.value == "managed"
    assert settings.gateway_exposure.value == "loopback"
    assert settings.path_profile == "development"
    assert settings.trusted_hosts == ["127.0.0.1", "localhost"]
