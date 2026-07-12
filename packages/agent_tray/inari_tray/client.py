from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import Any, Callable, Protocol, TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter
from inari.local_api.schemas import (
    DeviceDirectoryResponse,
    DeviceEventCollectionResponse,
    JobResourceResponse,
    LiveUpdateMessage,
    ManagedOnboardingDeviceConfirmationRequest,
    ManagedOnboardingInvitationRequest,
    ManagedOnboardingPreviewResponse,
    ManagedOnboardingStartResponse,
    ManagedOnboardingStatusResponse,
    SystemStatusResponse,
    TokenResponse,
)
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from .config import TraySettings
from .local_trust import (
    LocalIdentityStore,
    TrayLocalTrustClient,
    TrayPairingContext,
)

LIVE_UPDATE_MESSAGE_ADAPTER = TypeAdapter(LiveUpdateMessage)
ModelT = TypeVar("ModelT", bound=BaseModel)


class LocalTokenProvider(Protocol):
    def request_token(self, client: httpx.Client) -> TokenResponse: ...


class AgentApiClient:
    def __init__(
        self,
        settings: TraySettings,
        *,
        http_client_factory: Callable[[], httpx.Client] | None = None,
        websocket_connect: Callable[..., Any] | None = None,
        identity_store: LocalIdentityStore | None = None,
        pairing_context: TrayPairingContext | None = None,
        local_trust_client: LocalTokenProvider | None = None,
    ) -> None:
        self.settings = settings
        self._http_client_factory = http_client_factory or self._default_http_client
        self._websocket_connect = websocket_connect or connect
        self._local_trust_client = local_trust_client or TrayLocalTrustClient(
            settings,
            identity_store=identity_store,
            pairing_context=pairing_context,
        )
        self._cached_token: TokenResponse | None = None

    def get_status(self) -> SystemStatusResponse:
        with self._http_client_factory() as client:
            response = client.get(
                "/system/status", headers=self._authorization_headers(client)
            )
            response.raise_for_status()
        return SystemStatusResponse.model_validate(response.json())

    def list_devices(self) -> DeviceDirectoryResponse:
        with self._http_client_factory() as client:
            response = client.get(
                "/devices", headers=self._authorization_headers(client)
            )
            response.raise_for_status()
        return DeviceDirectoryResponse.model_validate(response.json())

    def list_device_events(
        self, device_id: str, *, limit: int = 50
    ) -> DeviceEventCollectionResponse:
        with self._http_client_factory() as client:
            response = client.get(
                f"/devices/{device_id}/events",
                params={"limit": limit},
                headers=self._authorization_headers(client),
            )
            response.raise_for_status()
        return DeviceEventCollectionResponse.model_validate(response.json())

    def submit_test_page(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse:
        payload: dict[str, object] = {"command": {"kind": "print_test_page"}}
        target: dict[str, object] = {}
        if device_id is not None:
            target["device_id"] = device_id
        if printer_name is not None:
            target["printer_name"] = printer_name
        if target:
            payload["target"] = target
        with self._http_client_factory() as client:
            response = client.post(
                "/device-commands",
                json=payload,
                headers=self._authorization_headers(client),
            )
            response.raise_for_status()
        return JobResourceResponse.model_validate(response.json())

    def open_cash_drawer(
        self,
        *,
        device_id: str | None = None,
        printer_name: str | None = None,
    ) -> JobResourceResponse:
        payload: dict[str, object] = {"command": {"kind": "open_cash_drawer"}}
        target: dict[str, object] = {}
        if device_id is not None:
            target["device_id"] = device_id
        if printer_name is not None:
            target["printer_name"] = printer_name
        if target:
            payload["target"] = target
        with self._http_client_factory() as client:
            response = client.post(
                "/device-commands",
                json=payload,
                headers=self._authorization_headers(client),
            )
            response.raise_for_status()
        return JobResourceResponse.model_validate(response.json())

    def preview_onboarding(
        self,
        invitation: str,
        *,
        controller_url: str | None = None,
    ) -> ManagedOnboardingPreviewResponse:
        request = ManagedOnboardingInvitationRequest(
            invitation=invitation,
            controller_url=controller_url,
        )
        return self._request_model(
            "POST",
            "/onboarding/managed/preview",
            ManagedOnboardingPreviewResponse,
            json=request.model_dump(mode="json"),
        )

    def start_onboarding(
        self,
        invitation: str,
        *,
        controller_url: str | None = None,
    ) -> ManagedOnboardingStartResponse:
        request = ManagedOnboardingInvitationRequest(
            invitation=invitation,
            controller_url=controller_url,
        )
        return self._request_model(
            "POST",
            "/onboarding/managed/start",
            ManagedOnboardingStartResponse,
            json=request.model_dump(mode="json"),
        )

    def get_onboarding_status(self) -> ManagedOnboardingStatusResponse:
        return self._request_model(
            "GET",
            "/onboarding/status",
            ManagedOnboardingStatusResponse,
        )

    def confirm_onboarding_devices(
        self,
        *,
        device_ids: tuple[str, ...],
        labels: dict[str, str],
        default_printer_device_id: str | None,
    ) -> ManagedOnboardingStatusResponse:
        request = ManagedOnboardingDeviceConfirmationRequest(
            device_ids=device_ids,
            labels=labels,
            default_printer_device_id=default_printer_device_id,
        )
        return self._request_model(
            "POST",
            "/onboarding/devices/confirm",
            ManagedOnboardingStatusResponse,
            json=request.model_dump(mode="json"),
        )

    def cancel_onboarding(self) -> ManagedOnboardingStatusResponse:
        return self._request_model(
            "POST",
            "/onboarding/cancel",
            ManagedOnboardingStatusResponse,
        )

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

    def _request_model(
        self,
        method: str,
        path: str,
        model_type: type[ModelT],
        *,
        json: object | None = None,
    ) -> ModelT:
        with self._http_client_factory() as client:
            response = client.request(
                method,
                path,
                headers=self._authorization_headers(client),
                json=json,
            )
            response.raise_for_status()
        return model_type.model_validate(response.json())

    def _ensure_token(self, client: httpx.Client | None = None) -> TokenResponse:
        if (
            self._cached_token is not None
            and self._cached_token.expires_at > _utc_now() + timedelta(seconds=30)
        ):
            return self._cached_token
        owns_client = client is None
        active_client = client or self._http_client_factory()
        try:
            self._cached_token = self._local_trust_client.request_token(active_client)
            return self._cached_token
        finally:
            if owns_client:
                active_client.close()


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
