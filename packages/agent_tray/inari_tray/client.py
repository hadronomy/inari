from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, Callable

import httpx
from pydantic import TypeAdapter
from inari.models import (
    JobResourceResponse,
    LiveUpdateMessage,
    SystemStatusResponse,
    TokenResponse,
)
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .config import TraySettings

LIVE_UPDATE_MESSAGE_ADAPTER = TypeAdapter(LiveUpdateMessage)


class AgentApiClient:
    def __init__(
        self,
        settings: TraySettings,
        *,
        http_client_factory: Callable[[], httpx.Client] | None = None,
        websocket_connect: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._http_client_factory = http_client_factory or self._default_http_client
        self._websocket_connect = websocket_connect or connect
        self._cached_token: TokenResponse | None = None

    def get_status(self) -> SystemStatusResponse:
        with self._http_client_factory() as client:
            response = client.get(
                "/system/status", headers=self._authorization_headers(client)
            )
            response.raise_for_status()
        return SystemStatusResponse.model_validate(response.json())

    def submit_test_page(
        self, *, printer_name: str | None = None
    ) -> JobResourceResponse:
        payload: dict[str, object] = {"command": {"kind": "print_test_page"}}
        if printer_name is not None:
            payload["target"] = {"printer_name": printer_name}
        with self._http_client_factory() as client:
            response = client.post(
                "/device-commands",
                json=payload,
                headers=self._authorization_headers(client),
            )
            response.raise_for_status()
        return JobResourceResponse.model_validate(response.json())

    def iter_live_updates(self, stop_event: Event) -> Iterator[LiveUpdateMessage]:
        token = self._ensure_token()
        with self._websocket_connect(
            self.settings.agent_events_url,
            open_timeout=self.settings.connect_timeout_seconds,
            close_timeout=self.settings.connect_timeout_seconds,
            additional_headers={"Authorization": f"Bearer {token.access_token}"},
        ) as websocket:
            while not stop_event.is_set():
                try:
                    raw_message = websocket.recv(
                        timeout=self.settings.event_timeout_seconds
                    )
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    return
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                yield LIVE_UPDATE_MESSAGE_ADAPTER.validate_json(raw_message)

    def _default_http_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.agent_api_base_url,
            timeout=self.settings.connect_timeout_seconds,
        )

    def _authorization_headers(self, client: httpx.Client) -> dict[str, str]:
        token = self._ensure_token(client)
        return {"Authorization": f"Bearer {token.access_token}"}

    def _ensure_token(self, client: httpx.Client | None = None) -> TokenResponse:
        if (
            self._cached_token is not None
            and self._cached_token.expires_at > _utc_now() + timedelta(seconds=30)
        ):
            return self._cached_token
        owns_client = client is None
        active_client = client or self._http_client_factory()
        try:
            response = active_client.post(
                "/auth/local-token",
                json={"client_name": self.settings.auth_client_name},
            )
            response.raise_for_status()
            self._cached_token = TokenResponse.model_validate(response.json())
            return self._cached_token
        finally:
            if owns_client:
                active_client.close()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
