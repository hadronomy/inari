from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from websockets.asyncio.client import connect as websocket_connect

from ..config import AgentSettings
from ..runtime.models import utc_now
from ..security.models import GatewayMode
from ..security.tls import TlsContextFactory
from .enrollment import GatewayEnrollmentService
from .models import UpstreamConnectionState, UpstreamStatus


class GatewayConnector:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        enrollment_service: GatewayEnrollmentService,
        tls_context_factory: TlsContextFactory,
        snapshot_provider: Callable[[], dict[str, Any]],
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.settings = settings
        self.enrollment_service = enrollment_service
        self.tls_context_factory = tls_context_factory
        self.snapshot_provider = snapshot_provider
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._status = UpstreamStatus(
            mode=settings.gateway_mode,
            state=(
                UpstreamConnectionState.DISCONNECTED
                if settings.gateway_mode is GatewayMode.MANAGED
                else UpstreamConnectionState.DISABLED
            ),
            base_url=settings.upstream_base_url,
            detail="Gateway operates locally by default." if settings.gateway_mode is GatewayMode.STANDALONE else None,
        )
        self._lock = asyncio.Lock()

    async def sync_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            await self._update_status(state=UpstreamConnectionState.DISABLED, detail="Managed upstream mode is disabled.")
            return
        await self._update_status(state=UpstreamConnectionState.ENROLLING, detail="Ensuring upstream enrollment.")
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None:
            await self._update_status(
                state=UpstreamConnectionState.DISCONNECTED,
                detail="Awaiting upstream bootstrap credentials.",
                last_error="No upstream enrollment credentials are configured.",
            )
            return
        if not enrollment.status_url:
            await self._update_status(
                state=UpstreamConnectionState.DEGRADED,
                detail="Upstream enrollment succeeded without a status endpoint.",
                enrolled_at=enrollment.enrolled_at,
                events_url=enrollment.events_url,
            )
            return
        async with self._http_client_factory(
            verify=self.tls_context_factory.create_outbound_context(),
            timeout=self.settings.gateway_reconnect_delay_seconds,
        ) as client:
            response = await client.post(
                enrollment.status_url,
                json=self.snapshot_provider(),
                headers={"Authorization": f"Bearer {enrollment.access_token}"},
            )
            response.raise_for_status()
        await self._update_status(
            state=UpstreamConnectionState.ONLINE,
            detail="Synchronized the latest gateway snapshot upstream.",
            enrolled_at=enrollment.enrolled_at,
            last_sync_at=utc_now(),
            status_url=enrollment.status_url,
            events_url=enrollment.events_url,
            last_error=None,
        )

    async def listen_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None or not enrollment.events_url:
            return
        await self._update_status(
            state=UpstreamConnectionState.CONNECTING,
            detail="Connecting to the upstream control stream.",
            enrolled_at=enrollment.enrolled_at,
            status_url=enrollment.status_url,
            events_url=enrollment.events_url,
        )
        async with websocket_connect(
            enrollment.events_url,
            additional_headers={"Authorization": f"Bearer {enrollment.access_token}"},
            ssl=self.tls_context_factory.create_outbound_context(),
            open_timeout=self.settings.gateway_reconnect_delay_seconds,
            close_timeout=self.settings.gateway_reconnect_delay_seconds,
        ) as websocket:
            await self._update_status(
                state=UpstreamConnectionState.ONLINE,
                detail="Connected to the upstream control stream.",
                enrolled_at=enrollment.enrolled_at,
                status_url=enrollment.status_url,
                events_url=enrollment.events_url,
                last_error=None,
            )
            while True:
                raw_message = await asyncio.wait_for(websocket.recv(), timeout=self.settings.gateway_event_timeout_seconds)
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                self._handle_upstream_message(raw_message)
                await self._update_status(last_event_at=utc_now())

    def current_status(self) -> UpstreamStatus:
        return self._status

    def _handle_upstream_message(self, raw_message: str) -> None:
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            return
        message_type = str(payload.get("type", "message"))
        detail = str(payload.get("detail", "Received an upstream control-plane message."))
        self._status = replace(self._status, detail=f"{message_type}: {detail}")

    async def _update_status(self, **changes: object) -> None:
        async with self._lock:
            self._status = replace(self._status, **changes)
