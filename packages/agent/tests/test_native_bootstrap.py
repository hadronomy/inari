from types import SimpleNamespace

import pytest

from inari.config import AgentSettings
from inari.host_service.windows_pairing import WindowsPairingBootstrapServer
from inari.security.local_trust.native_bootstrap import native_agent_endpoint


def test_native_endpoint_uses_the_configured_https_listener() -> None:
    settings = AgentSettings(
        agent_endpoint="https://agent.example.com:7310/",
        trusted_hosts=["agent.example.com"],
        tls_cert_path="agent.crt",
        tls_key_path="agent.key",
    )
    assert native_agent_endpoint(settings) == "https://agent.example.com:7310/"


def test_native_endpoint_requires_a_name_for_tls() -> None:
    settings = AgentSettings(tls_cert_path="agent.crt", tls_key_path="agent.key")
    with pytest.raises(ValueError, match="api.endpoint"):
        native_agent_endpoint(settings)


def test_loopback_endpoint_uses_the_actual_listener_port() -> None:
    assert native_agent_endpoint(AgentSettings(port=7410)) == "http://127.0.0.1:7410/"


@pytest.mark.parametrize(
    ("host", "endpoint"),
    [
        ("::1", "http://[::1]:7410/"),
        ("127.0.0.2", "http://127.0.0.2:7410/"),
        ("localhost", "http://localhost:7410/"),
    ],
)
def test_loopback_endpoint_uses_the_actual_listener_host(
    host: str, endpoint: str
) -> None:
    settings = AgentSettings(host=host, port=7410, trusted_hosts=[host])
    assert native_agent_endpoint(settings) == endpoint


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.1", "agent.example.com"])
def test_native_endpoint_rejects_non_loopback_http_listeners(host: str) -> None:
    with pytest.raises(ValueError, match="api.endpoint"):
        native_agent_endpoint(AgentSettings(host=host, trusted_hosts=[host]))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://agent.example.com:7310/",
        "https://user:secret@agent.example.com:7310/",
        "https://agent.example.com:7310/api",
        "https://agent.example.com:7310/?secret=value",
        "https://agent.example.com:7310/#fragment",
        "https://agent.example.com:7410/",
        "https://untrusted.example.com:7310/",
    ],
)
def test_native_endpoint_rejects_an_unsafe_or_wrong_listener(endpoint) -> None:
    with pytest.raises(ValueError):
        native_agent_endpoint(
            AgentSettings(
                agent_endpoint=endpoint,
                trusted_hosts=["agent.example.com"],
                tls_cert_path="agent.crt",
                tls_key_path="agent.key",
            )
        )


def test_endpoint_discovery_does_not_issue_or_rotate_pairing_material(mocker) -> None:
    mocker.patch("inari.host_service.windows_pairing.sys.platform", "win32")
    mocker.patch(
        "inari.host_service.windows_pairing._client_package_family",
        return_value="Inari.Test",
    )
    file_api = SimpleNamespace(
        ReadFile=mocker.Mock(return_value=(0, b"\x02")),
        WriteFile=mocker.Mock(),
        FlushFileBuffers=mocker.Mock(),
    )
    mocker.patch(
        "inari.host_service.windows_pairing.importlib.import_module",
        return_value=file_api,
    )
    trust = mocker.Mock()
    server = WindowsPairingBootstrapServer(
        trust,
        package_family="Inari.Test",
        agent_endpoint="https://agent.example.com:7310/",
    )

    server._serve_client("pipe")

    trust.start_native_pairing.assert_not_called()
    file_api.WriteFile.assert_called_once_with(
        "pipe", b'{"agent_endpoint":"https://agent.example.com:7310/"}'
    )


def test_endpoint_discovery_rejects_an_unrelated_package(mocker) -> None:
    mocker.patch("inari.host_service.windows_pairing.sys.platform", "win32")
    mocker.patch(
        "inari.host_service.windows_pairing._client_package_family",
        return_value="Other.Package",
    )
    file_api = SimpleNamespace(
        ReadFile=mocker.Mock(return_value=(0, b"\x02")),
        WriteFile=mocker.Mock(),
    )
    mocker.patch(
        "inari.host_service.windows_pairing.importlib.import_module",
        return_value=file_api,
    )
    server = WindowsPairingBootstrapServer(
        mocker.Mock(),
        package_family="Inari.Test",
        agent_endpoint="https://agent.example.com:7310/",
    )

    with pytest.raises(PermissionError):
        server._serve_client("pipe")
    file_api.WriteFile.assert_not_called()


@pytest.mark.parametrize("package", ["Inari.Test", "Other.Package", None])
def test_setup_restart_requires_the_sibling_package(mocker, package) -> None:
    mocker.patch("inari.host_service.windows_pairing.sys.platform", "win32")
    mocker.patch(
        "inari.host_service.windows_pairing._client_package_family",
        return_value=package,
    )
    file_api = SimpleNamespace(
        ReadFile=mocker.Mock(return_value=(0, b"\x03")),
        WriteFile=mocker.Mock(),
        FlushFileBuffers=mocker.Mock(),
    )
    mocker.patch(
        "inari.host_service.windows_pairing.importlib.import_module",
        return_value=file_api,
    )
    restart = mocker.Mock()
    trust = mocker.Mock()
    server = WindowsPairingBootstrapServer(
        trust,
        package_family="Inari.Test",
        agent_endpoint="https://agent.example.com:7310/",
        request_setup_restart=restart,
    )
    if package == "Inari.Test":
        server._serve_client("pipe")
        restart.assert_called_once_with()
        file_api.WriteFile.assert_called_once_with(
            "pipe", b'{"restart_requested":true}'
        )
        file_api.FlushFileBuffers.assert_called_once_with("pipe")
    else:
        with pytest.raises(PermissionError):
            server._serve_client("pipe")
        restart.assert_not_called()
        file_api.WriteFile.assert_not_called()
    trust.start_native_pairing.assert_not_called()


def test_rejected_setup_restart_has_no_success_reply(mocker) -> None:
    mocker.patch("inari.host_service.windows_pairing.sys.platform", "win32")
    mocker.patch(
        "inari.host_service.windows_pairing._client_package_family",
        return_value="Inari.Test",
    )
    file_api = SimpleNamespace(
        ReadFile=mocker.Mock(return_value=(0, b"\x03")),
        WriteFile=mocker.Mock(),
    )
    mocker.patch(
        "inari.host_service.windows_pairing.importlib.import_module",
        return_value=file_api,
    )
    server = WindowsPairingBootstrapServer(
        mocker.Mock(),
        package_family="Inari.Test",
        agent_endpoint="https://agent.example.com:7310/",
        request_setup_restart=mocker.Mock(side_effect=PermissionError("not required")),
    )
    with pytest.raises(PermissionError, match="not required"):
        server._serve_client("pipe")
    file_api.WriteFile.assert_not_called()
