from __future__ import annotations

import json
import unittest
from threading import Event

from iot_agent_tray.client import AgentApiClient
from iot_agent_tray.config import TraySettings


class AgentApiClientTests(unittest.TestCase):
    def test_client_fetches_local_token_before_calling_protected_status(self) -> None:
        http_client = FakeHttpClient()
        client = AgentApiClient(
            TraySettings(),
            http_client_factory=lambda: http_client,
            websocket_connect=lambda *args, **kwargs: FakeWebSocketConnection(kwargs),
        )

        status = client.get_status()

        self.assertEqual(status.service.version, "1.9.0a1")
        self.assertEqual(http_client.requests[0][1], "/auth/local-token")
        self.assertEqual(http_client.requests[1][1], "/system/status")
        self.assertEqual(http_client.requests[1][2]["Authorization"], "Bearer local-token")

    def test_client_reuses_cached_token_for_websocket_stream(self) -> None:
        http_client = FakeHttpClient()
        captured: dict[str, object] = {}

        def websocket_connect(*args, **kwargs):
            captured.update(kwargs)
            return FakeWebSocketConnection(kwargs)

        client = AgentApiClient(
            TraySettings(),
            http_client_factory=lambda: http_client,
            websocket_connect=websocket_connect,
        )

        next(client.iter_live_updates(Event()))

        self.assertEqual(http_client.requests[0][1], "/auth/local-token")
        self.assertEqual(captured["additional_headers"]["Authorization"], "Bearer local-token")


class FakeHttpClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def close(self) -> None:
        return None

    def get(self, path: str, headers: dict[str, str] | None = None):
        self.requests.append(("GET", path, headers or {}))
        return FakeResponse(
            {
                "ok": True,
                "status": "healthy",
                "service": {"name": "IoT Agent", "version": "1.9.0a1"},
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
            }
        )

    def post(self, path: str, json: dict[str, object], headers: dict[str, str] | None = None):
        self.requests.append(("POST", path, headers or {}))
        if path == "/auth/local-token":
            return FakeResponse(
                {
                    "access_token": "local-token",
                    "token_type": "Bearer",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "scopes": ["system:read", "devices:read", "events:read", "jobs:read", "jobs:submit", "commands:execute", "admin:read", "admin:write"],
                    "subject": f"local:{json['client_name']}",
                    "principal_kind": "local_client",
                }
            )
        return FakeResponse({"ok": True, "job": {"id": "job_1"}})


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, object]:
        return self._payload


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
                    "service": {"name": "IoT Agent", "version": "1.9.0a1"},
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


if __name__ == "__main__":
    unittest.main()
