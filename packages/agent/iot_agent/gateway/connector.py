from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from websockets.asyncio.client import connect as websocket_connect

from ..config import AgentSettings
from ..exceptions import AgentError
from ..runtime.models import utc_now
from ..security.certificate_lifecycle import ManagedCertificateLifecycleManager
from ..security.models import GatewayMode
from ..security.tls import TlsContextFactory
from .enrollment import GatewayEnrollmentService
from .models import (
    GatewayEnrollmentRecord,
    MutualTlsPolicy,
    UpstreamCertificateMode,
    UpstreamConnectionState,
    UpstreamStatus,
    resolve_mutual_tls_policy,
)
from .protocol import (
    AgentAcknowledgeMessage,
    AgentHelloMessage,
    AgentPongMessage,
    AgentStatusSnapshotMessage,
    CONTROLLER_STREAM_ADAPTER,
    ControllerAcknowledgeMessage,
    ControllerCancelJobMessage,
    ControllerExecuteDeviceCommandMessage,
    ControllerHelloMessage,
    ControllerPingMessage,
    ControllerSubmitPrintJobMessage,
)
from .repositories import GatewayRepository
from .runtime_bridge import GatewayCommandDispatcher

logger = logging.getLogger(__name__)


class GatewayConnector:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        enrollment_service: GatewayEnrollmentService,
        certificate_lifecycle_manager: ManagedCertificateLifecycleManager | None,
        tls_context_factory: TlsContextFactory,
        snapshot_provider: Callable[[], dict[str, Any]],
        gateway_repository: GatewayRepository,
        command_dispatcher: GatewayCommandDispatcher,
        http_client_factory: Callable[..., httpx.AsyncClient] | None = None,
        websocket_connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.enrollment_service = enrollment_service
        self.certificate_lifecycle_manager = certificate_lifecycle_manager
        self.tls_context_factory = tls_context_factory
        self.snapshot_provider = snapshot_provider
        self.gateway_repository = gateway_repository
        self.command_dispatcher = command_dispatcher
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._websocket_connect_factory = websocket_connect_factory or websocket_connect
        mutual_tls_policy = resolve_mutual_tls_policy(
            settings.upstream_mutual_tls_mode,
            certificate_mode=settings.upstream_certificate_mode,
            client_certificate_present=False,
        )
        self._status = UpstreamStatus(
            mode=settings.gateway_mode,
            state=(
                UpstreamConnectionState.DISCONNECTED
                if settings.gateway_mode is GatewayMode.MANAGED
                else UpstreamConnectionState.DISABLED
            ),
            base_url=settings.upstream_base_url,
            detail="Gateway operates locally by default."
            if settings.gateway_mode is GatewayMode.STANDALONE
            else None,
            auth_mode=settings.upstream_auth_mode,
            certificate_mode=settings.upstream_certificate_mode,
            edge_provider=settings.upstream_edge_provider,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            last_applied_controller_sequence=gateway_repository.last_applied_controller_sequence(),
        )
        self._lock = asyncio.Lock()
        self._last_snapshot_sent_at = utc_now()

    async def sync_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            await self._update_status(
                state=UpstreamConnectionState.DISABLED,
                detail="Managed upstream mode is disabled.",
            )
            return

        await self._update_status(
            state=UpstreamConnectionState.ENROLLING,
            detail="Ensuring upstream enrollment.",
        )
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
                protocol_version=enrollment.protocol_version,
                controller_name=enrollment.controller_name,
                controller_instance_id=enrollment.controller_instance_id,
            )
            return

        if self.certificate_lifecycle_manager is not None:
            await self.certificate_lifecycle_manager.ensure_current(
                enrollment=enrollment,
                trigger="status_sync",
            )

        snapshot = self.snapshot_provider()
        headers = await self.enrollment_service.upstream_headers(enrollment)
        client_certificate_present = (
            self.tls_context_factory.certificate_service.current_certificate()
            is not None
            if self.tls_context_factory.certificate_service is not None
            else False
        )
        certificate_bootstrap_pending = self._certificate_bootstrap_pending(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        mutual_tls_policy = self._mutual_tls_policy(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        try:
            async with self._http_client_factory(
                verify=self.tls_context_factory.create_outbound_context(),
                timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as client:
                response = await client.post(
                    enrollment.status_url,
                    json=snapshot,
                    headers={
                        **headers,
                        "X-IoT-Agent-Protocol-Version": str(
                            enrollment.protocol_version
                            or snapshot.get("protocol", {}).get("version", "")
                        ),
                    },
                )
                if response.status_code in {401, 403}:
                    await self.enrollment_service.handle_auth_failure(enrollment)
                    await self._update_status(
                        state=UpstreamConnectionState.AUTH_FAILED,
                        detail="Upstream authentication failed during status sync.",
                        last_error=f"Controller rejected gateway credentials with HTTP {response.status_code}.",
                        failed_sync_count=self._status.failed_sync_count + 1,
                    )
                    return
                response.raise_for_status()
                response_payload = response.json() if response.content else {}
        except Exception as exc:
            await self._update_status(
                state=UpstreamConnectionState.RECOVERING,
                detail="Retrying upstream status synchronization.",
                last_error=str(exc),
                failed_sync_count=self._status.failed_sync_count + 1,
            )
            return

        await self._update_status(
            state=UpstreamConnectionState.ONLINE,
            detail="Synchronized the latest gateway snapshot upstream.",
            enrolled_at=enrollment.enrolled_at,
            last_sync_at=utc_now(),
            status_url=enrollment.status_url,
            events_url=enrollment.events_url,
            last_error=None,
            protocol_version=_selected_protocol_version(response_payload)
            or enrollment.protocol_version,
            controller_name=_controller_name(response_payload)
            or enrollment.controller_name,
            controller_instance_id=_controller_instance_id(response_payload)
            or enrollment.controller_instance_id,
            client_certificate_present=client_certificate_present,
            certificate_bootstrap_pending=certificate_bootstrap_pending,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            successful_sync_count=self._status.successful_sync_count + 1,
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
        )

    async def listen_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None or not enrollment.events_url:
            return
        if self.certificate_lifecycle_manager is not None:
            await self.certificate_lifecycle_manager.ensure_current(
                enrollment=enrollment,
                trigger="control_stream",
            )

        client_certificate_present = (
            self.tls_context_factory.certificate_service.current_certificate()
            is not None
            if self.tls_context_factory.certificate_service is not None
            else False
        )
        certificate_bootstrap_pending = self._certificate_bootstrap_pending(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        mutual_tls_policy = self._mutual_tls_policy(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        await self._update_status(
            state=UpstreamConnectionState.CONNECTING,
            detail="Connecting to the upstream control stream.",
            enrolled_at=enrollment.enrolled_at,
            status_url=enrollment.status_url,
            events_url=enrollment.events_url,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            client_certificate_present=client_certificate_present,
            certificate_bootstrap_pending=certificate_bootstrap_pending,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
        )
        headers = await self.enrollment_service.upstream_headers(enrollment)
        try:
            async with self._websocket_connect_factory(
                enrollment.events_url,
                additional_headers=headers,
                ssl=self.tls_context_factory.create_outbound_context(),
                open_timeout=self.settings.gateway_reconnect_delay_seconds,
                close_timeout=self.settings.gateway_reconnect_delay_seconds,
            ) as websocket:
                await websocket.send(
                    json.dumps(
                        AgentHelloMessage(
                            message_id=_message_id("ghello"),
                            selected_protocol_version=enrollment.protocol_version
                            or self.snapshot_provider()["protocol"]["version"],
                            supported_versions=tuple(
                                self.snapshot_provider()["protocol"][
                                    "supported_versions"
                                ]
                            ),
                            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
                            snapshot=self.snapshot_provider(),
                        ).model_dump(mode="json")
                    )
                )
                self._last_snapshot_sent_at = utc_now()
                await self._update_status(
                    state=UpstreamConnectionState.ONLINE,
                    detail="Connected to the upstream control stream.",
                    enrolled_at=enrollment.enrolled_at,
                    status_url=enrollment.status_url,
                    events_url=enrollment.events_url,
                    last_error=None,
                    client_certificate_present=client_certificate_present,
                    certificate_bootstrap_pending=certificate_bootstrap_pending,
                    mutual_tls_mode=mutual_tls_policy.effective_mode,
                    successful_event_stream_count=self._status.successful_event_stream_count
                    + 1,
                    last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
                )
                sender = asyncio.create_task(
                    self._send_outbox_loop(websocket), name="iot-agent-gateway-outbox"
                )
                receiver = asyncio.create_task(
                    self._receive_loop(websocket, enrollment),
                    name="iot-agent-gateway-receiver",
                )
                heartbeat = asyncio.create_task(
                    self._send_snapshot_heartbeat_loop(websocket),
                    name="iot-agent-gateway-heartbeat",
                )
                done, pending = await asyncio.wait(
                    {sender, receiver, heartbeat},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in pending:
                    task.cancel()
                for task in pending:
                    await asyncio.gather(task, return_exceptions=True)
                for task in done:
                    task.result()
        except Exception as exc:
            if isinstance(exc, AgentError) and exc.code == "UPSTREAM_PROTOCOL_MISMATCH":
                await self._update_status(
                    state=UpstreamConnectionState.PROTOCOL_MISMATCH,
                    detail=exc.message,
                    last_error=exc.message,
                    failed_event_stream_count=self._status.failed_event_stream_count
                    + 1,
                )
                return
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
                401,
                403,
            }:
                await self.enrollment_service.handle_auth_failure(enrollment)
                await self._update_status(
                    state=UpstreamConnectionState.AUTH_FAILED,
                    detail="Upstream authentication failed during control-stream connection.",
                    last_error=f"Controller rejected gateway credentials with HTTP {exc.response.status_code}.",
                    failed_event_stream_count=self._status.failed_event_stream_count
                    + 1,
                )
                return
            await self._update_status(
                state=UpstreamConnectionState.RECOVERING,
                detail="Reconnecting to the upstream control stream.",
                last_error=str(exc),
                failed_event_stream_count=self._status.failed_event_stream_count + 1,
            )

    def current_status(self, *, certificate_lifecycle=None) -> UpstreamStatus:
        if certificate_lifecycle is None:
            certificate_lifecycle = (
                self.certificate_lifecycle_manager.current_status()
                if self.certificate_lifecycle_manager is not None
                else None
            )
        return replace(self._status, certificate_lifecycle=certificate_lifecycle)

    async def _receive_loop(
        self, websocket, enrollment: GatewayEnrollmentRecord
    ) -> None:
        while True:
            try:
                raw_message = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=self.settings.gateway_event_timeout_seconds,
                )
            except asyncio.TimeoutError:
                continue
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            payload = json.loads(raw_message)
            message = CONTROLLER_STREAM_ADAPTER.validate_python(payload)
            await self._handle_message(message, websocket, enrollment)

    async def _handle_message(
        self, message, websocket, enrollment: GatewayEnrollmentRecord
    ) -> None:
        await self._update_status(last_event_at=utc_now())
        if isinstance(message, ControllerAcknowledgeMessage):
            self.gateway_repository.mark_outbox_acked(message.acknowledged_message_id)
            return
        if isinstance(message, ControllerPingMessage):
            await websocket.send(
                json.dumps(
                    AgentPongMessage(
                        message_id=_message_id("gpong"),
                        acknowledged_message_id=message.message_id,
                        detail=message.detail,
                    ).model_dump(mode="json")
                )
            )
            return
        if isinstance(message, ControllerHelloMessage):
            selected_protocol_version = message.selected_protocol_version
            if (
                selected_protocol_version
                not in self.snapshot_provider()["protocol"]["supported_versions"]
            ):
                raise AgentError(
                    "UPSTREAM_PROTOCOL_MISMATCH",
                    f"Controller protocol version {selected_protocol_version!r} is not supported.",
                    status_code=409,
                )
            if (
                enrollment.protocol_version is not None
                and selected_protocol_version != enrollment.protocol_version
            ):
                raise AgentError(
                    "UPSTREAM_PROTOCOL_MISMATCH",
                    f"Controller selected protocol version {selected_protocol_version!r}, but enrollment negotiated {enrollment.protocol_version!r}.",
                    status_code=409,
                )
            await websocket.send(
                json.dumps(
                    AgentAcknowledgeMessage(
                        message_id=_message_id("gack"),
                        acknowledged_message_id=message.message_id,
                    ).model_dump(mode="json")
                )
            )
            await self._update_status(
                protocol_version=selected_protocol_version,
                controller_name=message.controller.name
                if message.controller is not None
                else None,
                controller_instance_id=message.controller.instance_id
                if message.controller is not None
                else None,
                controller_resume_from_sequence=message.resume_from_sequence,
            )
            return
        if isinstance(message, ControllerSubmitPrintJobMessage):
            await self.command_dispatcher.handle_submit_print_job(
                message, enrollment=enrollment
            )
        elif isinstance(message, ControllerExecuteDeviceCommandMessage):
            await self.command_dispatcher.handle_execute_device_command(
                message, enrollment=enrollment
            )
        elif isinstance(message, ControllerCancelJobMessage):
            await self.command_dispatcher.handle_cancel_job(
                message, enrollment=enrollment
            )
        await self._update_status(
            last_command_at=utc_now(),
            last_command_id=getattr(message, "command_id", None),
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
        )

    async def _send_outbox_loop(self, websocket) -> None:
        while True:
            pending = self.gateway_repository.list_pending_outbox(
                limit=self.settings.gateway_outbox_batch_size
            )
            if not pending:
                await asyncio.sleep(self.settings.gateway_control_poll_interval_seconds)
                continue
            for record in pending:
                try:
                    await websocket.send(json.dumps(record.payload))
                    self.gateway_repository.mark_outbox_sent(record.message_id)
                except Exception as exc:
                    self.gateway_repository.mark_outbox_failed(
                        record.message_id, detail=str(exc)
                    )
                    raise

    async def _send_snapshot(self, websocket) -> None:
        snapshot = self.snapshot_provider()
        await websocket.send(
            json.dumps(
                AgentStatusSnapshotMessage(
                    message_id=_message_id("gsnap"),
                    snapshot=snapshot,
                ).model_dump(mode="json")
            )
        )
        self._last_snapshot_sent_at = utc_now()

    async def _send_snapshot_heartbeat_loop(self, websocket) -> None:
        interval = max(self.settings.gateway_event_timeout_seconds, 1.0)
        while True:
            await asyncio.sleep(interval)
            if (
                utc_now() - self._last_snapshot_sent_at
            ).total_seconds() >= interval:
                await self._send_snapshot(websocket)

    async def _update_status(self, **changes: object) -> None:
        async with self._lock:
            self._status = replace(self._status, **changes)

    def _certificate_bootstrap_pending(
        self,
        enrollment: GatewayEnrollmentRecord | None,
        *,
        client_certificate_present: bool,
    ) -> bool:
        if (
            self.settings.upstream_certificate_mode
            is not UpstreamCertificateMode.STEP_CA
        ):
            return False
        if client_certificate_present:
            return False
        return enrollment is not None

    def _mutual_tls_policy(
        self,
        enrollment: GatewayEnrollmentRecord | None,
        *,
        client_certificate_present: bool,
    ) -> MutualTlsPolicy:
        return resolve_mutual_tls_policy(
            enrollment.mutual_tls_mode if enrollment is not None else self.settings.upstream_mutual_tls_mode,
            certificate_mode=(
                enrollment.certificate_mode
                if enrollment is not None
                else self.settings.upstream_certificate_mode
            ),
            client_certificate_present=client_certificate_present,
            certificate_bootstrap=(
                enrollment.certificate_bootstrap if enrollment is not None else None
            ),
        )


def _message_id(prefix: str) -> str:
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex}"


def _selected_protocol_version(payload: dict[str, Any]) -> str | None:
    if payload.get("selected_protocol_version") is not None:
        return str(payload["selected_protocol_version"])
    if payload.get("protocol_version") is not None:
        return str(payload["protocol_version"])
    return None


def _controller_name(payload: dict[str, Any]) -> str | None:
    controller = payload.get("controller")
    if isinstance(controller, dict) and controller.get("name") is not None:
        return str(controller["name"])
    if payload.get("controller_name") is not None:
        return str(payload["controller_name"])
    return None


def _controller_instance_id(payload: dict[str, Any]) -> str | None:
    controller = payload.get("controller")
    if isinstance(controller, dict) and controller.get("instance_id") is not None:
        return str(controller["instance_id"])
    if payload.get("controller_instance_id") is not None:
        return str(payload["controller_instance_id"])
    return None
