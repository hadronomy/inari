from __future__ import annotations

import json
from threading import Event
from typing import cast

import httpx
import respx

from inari.core.version import API_VERSION
from inari_tray.client import AgentApiClient
from inari_tray.config import TraySettings


def test_client_fetches_local_token_before_calling_protected_status() -> None:
    settings = TraySettings(agent_api_base_url="http://agent.test")
    with respx.mock(assert_all_called=True, base_url="http://agent.test") as respx_mock:
        token_route = respx_mock.post("/auth/local-token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["system:read", "devices:read"],
                    "subject": "local:inari-tray",
                    "principal_kind": "local_client",
                },
            )
        )
        status_route = respx_mock.get("/system/status").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "status": "healthy",
                    "service": {"name": "Inari", "version": API_VERSION},
                    "devices": {
                        "count": 0,
                        "online_count": 0,
                        "offline_count": 0,
                        "kind_counts": {},
                        "default_device": None,
                    },
                    "queue": {
                        "total": 0,
                        "queued": 0,
                        "dispatched": 0,
                        "running": 0,
                        "retry_scheduled": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "cancelled": 0,
                    },
                    "supported_content_kinds": ["text"],
                    "supported_device_commands": ["print_test_page"],
                },
            )
        )
        client = AgentApiClient(
            settings,
            http_client_factory=lambda: httpx.Client(
                base_url=settings.agent_api_base_url
            ),
            websocket_connect=lambda *args, **kwargs: FakeWebSocketConnection(kwargs),
        )

        status = client.get_status()

    assert status.service.version == API_VERSION
    assert token_route.called
    assert status_route.called
    assert (
        status_route.calls.last.request.headers["Authorization"] == "Bearer local-token"
    )


def test_client_reuses_cached_token_for_websocket_stream() -> None:
    settings = TraySettings(agent_api_base_url="http://agent.test")
    captured: dict[str, object] = {}

    def websocket_connect(*args, **kwargs):
        captured.update(kwargs)
        return FakeWebSocketConnection(kwargs)

    with respx.mock(assert_all_called=True, base_url="http://agent.test") as respx_mock:
        respx_mock.post("/auth/local-token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["system:read", "devices:read", "events:read"],
                    "subject": "local:inari-tray",
                    "principal_kind": "local_client",
                },
            )
        )
        client = AgentApiClient(
            settings,
            http_client_factory=lambda: httpx.Client(
                base_url=settings.agent_api_base_url
            ),
            websocket_connect=websocket_connect,
        )

        next(client.iter_live_updates(Event()))

    additional_headers = captured["additional_headers"]
    assert isinstance(additional_headers, dict)
    typed_headers = cast(dict[str, str], additional_headers)
    assert typed_headers["Authorization"] == "Bearer local-token"


def test_client_lists_devices_with_enriched_driver_metadata() -> None:
    settings = TraySettings(agent_api_base_url="http://agent.test")
    with respx.mock(assert_all_called=True, base_url="http://agent.test") as respx_mock:
        respx_mock.post("/auth/local-token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["devices:read"],
                    "subject": "local:inari-tray",
                    "principal_kind": "local_client",
                },
            )
        )
        respx_mock.get("/devices").mock(
            return_value=httpx.Response(
                200,
                json={
                    "devices": [
                        {
                            "id": "dev_printer",
                            "kind": "printer",
                            "device_class": "physical",
                            "name": "Kitchen Printer",
                            "driver_key": "tests.fake-printers",
                            "driver": {
                                "key": "tests.fake-printers",
                                "display_name": "Test Printer Driver",
                                "kind": "printer",
                                "platform": "test",
                            },
                            "connection": {
                                "state": "online",
                                "first_seen_at": "2026-04-18T10:00:00Z",
                                "last_seen_at": "2026-04-18T10:05:00Z",
                                "observed_at": "2026-04-18T10:05:00Z",
                            },
                            "printer": {
                                "is_default": True,
                                "preferred_transport": "raw",
                                "supported_transports": ["raw", "document"],
                                "capabilities": ["cash_drawer"],
                            },
                            "metadata": {"source": "tests"},
                        }
                    ],
                    "summary": {
                        "count": 1,
                        "online_count": 1,
                        "offline_count": 0,
                        "kind_counts": {"printer": 1},
                        "default_device": {
                            "id": "dev_printer",
                            "name": "Kitchen Printer",
                        },
                    },
                },
            )
        )
        client = AgentApiClient(
            settings,
            http_client_factory=lambda: httpx.Client(
                base_url=settings.agent_api_base_url
            ),
        )

        directory = client.list_devices()

    assert directory.summary.count == 1
    assert directory.devices[0].driver is not None
    assert directory.devices[0].driver.display_name == "Test Printer Driver"
    assert directory.devices[0].metadata["source"] == "tests"


def test_client_lists_device_events() -> None:
    settings = TraySettings(agent_api_base_url="http://agent.test")
    with respx.mock(assert_all_called=True, base_url="http://agent.test") as respx_mock:
        respx_mock.post("/auth/local-token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["devices:read"],
                    "subject": "local:inari-tray",
                    "principal_kind": "local_client",
                },
            )
        )
        route = respx_mock.get("/devices/dev_printer/events").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "events": [
                        {
                            "sequence": 4,
                            "resource_kind": "device",
                            "resource_id": "dev_printer",
                            "event_type": "device.connected",
                            "occurred_at": "2026-04-18T10:05:00Z",
                            "payload": {"name": "Kitchen Printer"},
                        }
                    ],
                },
            )
        )
        client = AgentApiClient(
            settings,
            http_client_factory=lambda: httpx.Client(
                base_url=settings.agent_api_base_url
            ),
        )

        events = client.list_device_events("dev_printer", limit=25)

    assert len(events.events) == 1
    assert events.events[0].event_type == "device.connected"
    assert route.calls.last.request.url.params["limit"] == "25"


def test_client_device_commands_target_device_id() -> None:
    settings = TraySettings(agent_api_base_url="http://agent.test")
    with respx.mock(assert_all_called=True, base_url="http://agent.test") as respx_mock:
        respx_mock.post("/auth/local-token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["commands:execute"],
                    "subject": "local:inari-tray",
                    "principal_kind": "local_client",
                },
            )
        )
        command_route = respx_mock.post("/device-commands").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ok": True,
                    "job": {
                        "id": "job_123",
                        "kind": "device_command",
                        "operation": "print_test_page",
                        "state": "queued",
                        "target": {
                            "device_id": "dev_printer",
                            "device_kind": "printer",
                            "device_name": "Kitchen Printer",
                        },
                        "command_kind": "print_test_page",
                        "attempt_count": 0,
                        "max_attempts": 3,
                        "created_at": "2026-04-18T10:00:00Z",
                        "updated_at": "2026-04-18T10:00:00Z",
                        "queued_at": "2026-04-18T10:00:00Z",
                        "next_run_at": "2026-04-18T10:00:00Z",
                        "metadata": {},
                    },
                },
            )
        )
        client = AgentApiClient(
            settings,
            http_client_factory=lambda: httpx.Client(
                base_url=settings.agent_api_base_url
            ),
        )

        client.submit_test_page(device_id="dev_printer")
        client.open_cash_drawer(device_id="dev_printer")

    posted_payloads = [
        json.loads(call.request.content.decode("utf-8")) for call in command_route.calls
    ]
    assert posted_payloads[0]["target"] == {"device_id": "dev_printer"}
    assert posted_payloads[0]["command"]["kind"] == "print_test_page"
    assert posted_payloads[1]["target"] == {"device_id": "dev_printer"}
    assert posted_payloads[1]["command"]["kind"] == "open_cash_drawer"


class FakeWebSocketConnection:
    def __init__(self, kwargs: dict[str, object]) -> None:
        self.kwargs = kwargs
        self._delivered = False

    def __enter__(self) -> FakeWebSocketConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def recv(self, timeout: float | None = None):
        if self._delivered:
            raise TimeoutError
        self._delivered = True
        return json.dumps(
            {
                "kind": "snapshot",
                "status": {
                    "ok": True,
                    "status": "healthy",
                    "service": {"name": "Inari", "version": API_VERSION},
                    "devices": {
                        "count": 0,
                        "online_count": 0,
                        "offline_count": 0,
                        "kind_counts": {},
                        "default_device": None,
                    },
                    "queue": {
                        "total": 0,
                        "queued": 0,
                        "dispatched": 0,
                        "running": 0,
                        "retry_scheduled": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "cancelled": 0,
                    },
                    "supported_content_kinds": ["text"],
                    "supported_device_commands": ["print_test_page"],
                },
            }
        )
