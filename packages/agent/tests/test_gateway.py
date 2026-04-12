from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from iot_agent.config import AgentSettings
from iot_agent.gateway.connector import GatewayConnector
from iot_agent.gateway.models import GatewayEnrollmentRecord, UpstreamConnectionState
from iot_agent.security.tls import TlsContextFactory
from iot_agent.runtime.models import utc_now


class GatewayConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_connector_stays_disconnected_without_enrollment(self) -> None:
        connector = GatewayConnector(
            settings=AgentSettings(gateway_mode="managed", upstream_base_url="https://controller.example"),
            enrollment_service=FakeEnrollmentService(None),
            tls_context_factory=TlsContextFactory(AgentSettings()),
            snapshot_provider=lambda: {"status": "ok"},
            http_client_factory=lambda **kwargs: FakeAsyncHttpClient(),
        )

        await connector.sync_once()

        self.assertEqual(connector.current_status().state, UpstreamConnectionState.DISCONNECTED)

    async def test_connector_marks_online_after_successful_status_sync(self) -> None:
        enrollment = GatewayEnrollmentRecord(
            access_token="upstream-token",
            enrolled_at=utc_now(),
            status_url="https://controller.example/status",
            events_url="wss://controller.example/events",
        )
        http_client = FakeAsyncHttpClient()
        connector = GatewayConnector(
            settings=AgentSettings(gateway_mode="managed", upstream_base_url="https://controller.example"),
            enrollment_service=FakeEnrollmentService(enrollment),
            tls_context_factory=TlsContextFactory(AgentSettings()),
            snapshot_provider=lambda: {"status": "ok"},
            http_client_factory=lambda **kwargs: http_client,
        )

        await connector.sync_once()

        self.assertEqual(connector.current_status().state, UpstreamConnectionState.ONLINE)
        self.assertEqual(http_client.requests[0][0], "https://controller.example/status")


class FakeEnrollmentService:
    def __init__(self, record: GatewayEnrollmentRecord | None) -> None:
        self.record = record

    async def ensure_enrolled(self):
        return self.record


class FakeAsyncHttpClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object], dict[str, str]]] = []

    async def __aenter__(self) -> FakeAsyncHttpClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]):
        self.requests.append((url, json, headers))
        return FakeAsyncResponse()


class FakeAsyncResponse:
    def raise_for_status(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
