from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from ..config import AgentSettings
from ..runtime.models import utc_now
from ..security.certificate_lifecycle import ManagedCertificateLifecycleManager
from ..security.models import GatewayMode
from .data_plane import ZenohGatewayTransport
from .data_plane.base import GatewayDataPlaneTransport
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
    AGENT_PUBLICATION_ADAPTER,
    AgentStatusSnapshotMessage,
    ControllerCancelJobMessage,
    ControllerExecuteDeviceCommandMessage,
    ControllerSubmitPrintJobMessage,
    GatewaySnapshotPayload,
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
        snapshot_provider: Callable[[], GatewaySnapshotPayload],
        gateway_repository: GatewayRepository,
        command_dispatcher: GatewayCommandDispatcher,
        data_plane_transport: GatewayDataPlaneTransport | None = None,
    ) -> None:
        self.settings = settings
        self.enrollment_service = enrollment_service
        self.certificate_lifecycle_manager = certificate_lifecycle_manager
        self.snapshot_provider = snapshot_provider
        self.gateway_repository = gateway_repository
        self.command_dispatcher = command_dispatcher
        self.data_plane_transport = data_plane_transport
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
            certificate_mode=settings.upstream_certificate_mode,
            edge_provider=settings.upstream_edge_provider,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            last_applied_controller_sequence=gateway_repository.last_applied_controller_sequence(),
        )
        self._lock = asyncio.Lock()

    async def sync_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            await self._update_status(
                state=UpstreamConnectionState.DISABLED,
                detail="Managed upstream mode is disabled.",
            )
            return

        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None:
            await self._update_status(
                state=UpstreamConnectionState.DISCONNECTED,
                detail="Awaiting upstream bootstrap credentials.",
                last_error="No upstream enrollment credentials are configured.",
            )
            return
        await self._ensure_certificate_current(enrollment, trigger="status_publish")
        snapshot_message = AgentStatusSnapshotMessage(
            message_id=_message_id("gstatus"),
            snapshot=self.snapshot_provider(),
        )
        client_certificate_present = self._client_certificate_present()
        certificate_bootstrap_pending = self._certificate_bootstrap_pending(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        mutual_tls_policy = self._mutual_tls_policy(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        try:
            await self._transport().publish_status(
                enrollment=enrollment,
                message=snapshot_message,
            )
        except Exception as exc:
            await self._update_status(
                state=UpstreamConnectionState.RECOVERING,
                detail="Retrying gateway status publication on the managed data plane.",
                last_error=str(exc),
                failed_status_publication_count=self._status.failed_status_publication_count
                + 1,
                data_plane_kind=enrollment.data_plane.kind,
                data_plane_namespace=enrollment.data_plane.namespace,
                data_plane_session_mode=enrollment.data_plane.session_mode,
            )
            raise

        await self._update_status(
            state=UpstreamConnectionState.ONLINE,
            detail="Published the latest gateway status on the managed data plane.",
            enrolled_at=enrollment.enrolled_at,
            last_status_published_at=utc_now(),
            last_data_plane_activity_at=utc_now(),
            last_error=None,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            client_certificate_present=client_certificate_present,
            certificate_bootstrap_pending=certificate_bootstrap_pending,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            successful_status_publication_count=self._status.successful_status_publication_count
            + 1,
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
            data_plane_kind=enrollment.data_plane.kind,
            data_plane_namespace=enrollment.data_plane.namespace,
            data_plane_session_mode=enrollment.data_plane.session_mode,
        )

    async def listen_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None:
            return
        await self._ensure_certificate_current(enrollment, trigger="data_plane")
        client_certificate_present = self._client_certificate_present()
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
            detail="Connecting to the managed Zenoh data plane.",
            enrolled_at=enrollment.enrolled_at,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            client_certificate_present=client_certificate_present,
            certificate_bootstrap_pending=certificate_bootstrap_pending,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
            data_plane_kind=enrollment.data_plane.kind,
            data_plane_namespace=enrollment.data_plane.namespace,
            data_plane_session_mode=enrollment.data_plane.session_mode,
        )
        try:
            await self._transport().run_forever(
                enrollment=enrollment,
                last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
                on_connected=lambda: self._handle_transport_connected(enrollment),
                on_command=self._handle_command,
            )
        except Exception as exc:
            await self._update_status(
                state=UpstreamConnectionState.RECOVERING,
                detail="Reconnecting to the managed Zenoh data plane.",
                last_error=str(exc),
                failed_data_plane_connection_count=self._status.failed_data_plane_connection_count
                + 1,
                data_plane_kind=enrollment.data_plane.kind,
                data_plane_namespace=enrollment.data_plane.namespace,
                data_plane_session_mode=enrollment.data_plane.session_mode,
            )
            raise

    async def flush_outbox_once(self) -> None:
        if self.settings.gateway_mode is not GatewayMode.MANAGED:
            return
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None:
            return
        pending = self.gateway_repository.list_pending_outbox(
            limit=self.settings.gateway_outbox_batch_size
        )
        if not pending:
            return
        transport = self._transport()
        for record in pending:
            message = AGENT_PUBLICATION_ADAPTER.validate_python(record.payload)
            try:
                await transport.publish_publications(
                    enrollment=enrollment,
                    messages=(message,),
                )
            except Exception as exc:
                self.gateway_repository.mark_outbox_failed(
                    record.message_id,
                    detail=str(exc),
                )
                await self._update_status(
                    last_error=str(exc),
                    retry_delay_seconds=self._status.retry_delay_seconds,
                )
                raise
            self.gateway_repository.mark_outbox_sent(record.message_id)
        await self._update_status(last_data_plane_activity_at=utc_now())

    async def close(self) -> None:
        if self.data_plane_transport is not None:
            await self.data_plane_transport.close()

    def current_status(self, *, certificate_lifecycle=None) -> UpstreamStatus:
        if certificate_lifecycle is None:
            certificate_lifecycle = (
                self.certificate_lifecycle_manager.current_status()
                if self.certificate_lifecycle_manager is not None
                else None
            )
        return replace(self._status, certificate_lifecycle=certificate_lifecycle)

    async def _handle_command(self, message) -> None:
        enrollment = await self.enrollment_service.ensure_enrolled()
        if enrollment is None:
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
            last_command_id=message.command_id,
            last_applied_controller_sequence=self.gateway_repository.last_applied_controller_sequence(),
            last_data_plane_activity_at=utc_now(),
        )

    async def _handle_transport_connected(
        self, enrollment: GatewayEnrollmentRecord
    ) -> None:
        client_certificate_present = self._client_certificate_present()
        certificate_bootstrap_pending = self._certificate_bootstrap_pending(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        mutual_tls_policy = self._mutual_tls_policy(
            enrollment,
            client_certificate_present=client_certificate_present,
        )
        await self._update_status(
            state=UpstreamConnectionState.ONLINE,
            detail="Connected to the managed Zenoh data plane.",
            enrolled_at=enrollment.enrolled_at,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            client_certificate_present=client_certificate_present,
            certificate_bootstrap_pending=certificate_bootstrap_pending,
            mutual_tls_mode=mutual_tls_policy.effective_mode,
            last_error=None,
            last_data_plane_activity_at=utc_now(),
            successful_data_plane_connection_count=self._status.successful_data_plane_connection_count
            + 1,
            data_plane_kind=enrollment.data_plane.kind,
            data_plane_namespace=enrollment.data_plane.namespace,
            data_plane_session_mode=enrollment.data_plane.session_mode,
        )

    async def _ensure_certificate_current(
        self, enrollment: GatewayEnrollmentRecord, *, trigger: str
    ) -> None:
        if self.certificate_lifecycle_manager is None:
            return
        await self.certificate_lifecycle_manager.ensure_current(
            enrollment=enrollment,
            trigger=trigger,
        )

    def _client_certificate_present(self) -> bool:
        certificate_service = self.enrollment_service.certificate_service
        return certificate_service.current_certificate() is not None

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
        return bool(enrollment is not None and enrollment.bootstrap_pending)

    def _mutual_tls_policy(
        self,
        enrollment: GatewayEnrollmentRecord | None,
        *,
        client_certificate_present: bool,
    ) -> MutualTlsPolicy:
        return resolve_mutual_tls_policy(
            enrollment.mutual_tls_mode
            if enrollment is not None
            else self.settings.upstream_mutual_tls_mode,
            certificate_mode=(
                enrollment.certificate_mode
                if enrollment is not None
                else self.settings.upstream_certificate_mode
            ),
            client_certificate_present=client_certificate_present,
            certificate_enrollment=(
                enrollment.certificate_enrollment if enrollment is not None else None
            ),
        )

    def _transport(self) -> GatewayDataPlaneTransport:
        if self.data_plane_transport is None:
            self.data_plane_transport = ZenohGatewayTransport(
                settings=self.settings,
                certificate_service=self.enrollment_service.certificate_service,
            )
        return self.data_plane_transport


def _message_id(prefix: str) -> str:
    from uuid import uuid4

    return f"{prefix}_{uuid4().hex}"
