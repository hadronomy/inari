from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Any, Callable

import httpx
from iot_agent.models import JobResourceResponse, RuntimeEventResponse, SystemStatusResponse
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .config import TraySettings


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

    def get_status(self) -> SystemStatusResponse:
        with self._http_client_factory() as client:
            response = client.get("/system/status")
            response.raise_for_status()
        return SystemStatusResponse.model_validate(response.json())

    def submit_test_page(self, *, printer_name: str | None = None) -> JobResourceResponse:
        payload: dict[str, object] = {"command": {"kind": "print_test_page"}}
        if printer_name is not None:
            payload["target"] = {"printer_name": printer_name}
        with self._http_client_factory() as client:
            response = client.post("/device-commands", json=payload)
            response.raise_for_status()
        return JobResourceResponse.model_validate(response.json())

    def iter_events(self, stop_event: Event) -> Iterator[RuntimeEventResponse]:
        with self._websocket_connect(
            self.settings.agent_events_url,
            open_timeout=self.settings.connect_timeout_seconds,
            close_timeout=self.settings.connect_timeout_seconds,
        ) as websocket:
            while not stop_event.is_set():
                try:
                    raw_message = websocket.recv(timeout=self.settings.event_timeout_seconds)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    return
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                yield RuntimeEventResponse.model_validate_json(raw_message)

    def _default_http_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.settings.agent_api_base_url,
            timeout=self.settings.connect_timeout_seconds,
        )
