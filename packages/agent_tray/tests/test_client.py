from __future__ import annotations

import json
from threading import Event

import httpx
import respx

from inari.version import API_VERSION
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

    assert captured["additional_headers"]["Authorization"] == "Bearer local-token"


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
