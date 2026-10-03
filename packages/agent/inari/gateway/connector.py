from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from ..config import AgentSettings
from ..runtime.models import utc_now
from ..security.certificates.lifecycle import ManagedCertificateLifecycleManager
from ..security.models import GatewayMode
from .data_plane import ZenohGatewayTransport
from .data_plane.base import GatewayDataPlaneTransport
from .enrollment import GatewayEnrollmentService
from .models import (
    GatewayEnrollmentRecord,
    ManagedCertificateState,
    MutualTlsPolicy,
    UpstreamConnectionState,
    UpstreamStatus,
    resolve_mutual_tls_policy,
)
from .protocol import (
    AGENT_PUBLICATION_ADAPTER,
    AgentStatusSnapshotMessage,
    ControllerCancelJobMessage,
    ControllerExecuteDeviceCommandMessage,
    ControllerDispatchDeviceWorkMessage,
    GatewaySnapshotPayload,
)
from .repositories import GatewayRepository
from .state_events import GatewayStateEventProjector
from .bridges.runtime import GatewayCommandDispatcher

logger = logging.getLogger(__name__)


class GatewayConnector:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        enrollment_service: GatewayEnrollmentService,
        certificate_lifecycle_manager: ManagedCertificateLifecycleManager,
        snapshot_provider: Callable[[], GatewaySnapshotPayload],
        gateway_repository: GatewayRepository,
        command_dispatcher: GatewayCommandDispatcher,
        state_event_projector: GatewayStateEventProjector,
        data_plane_transport: GatewayDataPlaneTransport | None = None,
    ) -> None:
        self.settings = settings
        self.enrollment_service = enrollment_service
        self.certificate_lifecycle_manager = certificate_lifecycle_manager
        self.snapshot_provider = snapshot_provider
        self.gateway_repository = gateway_repository
        self.command_dispatcher = command_dispatcher
        self.state_event_projector = state_event_projector
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
        if not await self._admit_transport(enrollment, trigger="status_publish"):
            return
        snapshot_message = AgentStatusSnapshotMessage(
            message_id=_message_id("gstatus"),
            snapshot=self.snapshot_provider(),
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
            **self._admitted_certificate_status(enrollment),
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
        if not await self._admit_transport(enrollment, trigger="data_plane"):
            return
        enrollment = self.enrollment_service.load_enrollment() or enrollment
        await self._update_status(
            state=UpstreamConnectionState.CONNECTING,
            detail="Connecting to the managed Zenoh data plane.",
            enrolled_at=enrollment.enrolled_at,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            **self._admitted_certificate_status(enrollment),
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
                on_command=lambda message: self._handle_command(
                    message, session_enrollment=enrollment
                ),
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
        if enrollment.managed_dispatch is not None:
            self.state_event_projector.project(
                scope=enrollment.managed_dispatch.scope,
                dispatch_epoch=enrollment.managed_dispatch.epoch,
                limit=min(self.settings.gateway_outbox_batch_size, 1024),
            )
        pending = self.gateway_repository.list_pending_outbox(
            limit=self.settings.gateway_outbox_batch_size,
            recipient_scope=(
                None
                if enrollment.managed_dispatch is None
                else enrollment.managed_dispatch.scope
            ),
        )
        if not pending:
            return
        if not await self._admit_transport(enrollment, trigger="outbox"):
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
            certificate_lifecycle = self.certificate_lifecycle_manager.current_status()
        return replace(self._status, certificate_lifecycle=certificate_lifecycle)

    async def _handle_command(
        self, message, *, session_enrollment: GatewayEnrollmentRecord
    ) -> None:
        # An open session can outlive its admission, so each command is
        # admitted again. Only the cached enrollment is read: a superseded
        # enrollment closes the session instead of enrolling mid-session.
        enrollment = self.enrollment_service.load_enrollment()
        if enrollment is None or enrollment != session_enrollment:
            await self._close_transport(
                None,
                detail="The managed enrollment is no longer current.",
            )
            return
        if not await self._admit_transport(enrollment, trigger="command"):
            return
        if isinstance(message, ControllerExecuteDeviceCommandMessage):
            await self.command_dispatcher.handle_execute_device_command(
                message, enrollment=enrollment
            )
        elif isinstance(message, ControllerCancelJobMessage):
            await self.command_dispatcher.handle_cancel_job(
                message, enrollment=enrollment
            )
        elif isinstance(message, ControllerDispatchDeviceWorkMessage):
            await self.command_dispatcher.handle_dispatch_device_work(
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
        await self._update_status(
            state=UpstreamConnectionState.ONLINE,
            detail="Connected to the managed Zenoh data plane.",
            enrolled_at=enrollment.enrolled_at,
            protocol_version=enrollment.protocol_version,
            controller_name=enrollment.controller_name,
            controller_instance_id=enrollment.controller_instance_id,
            **self._admitted_certificate_status(enrollment),
            last_error=None,
            last_data_plane_activity_at=utc_now(),
            successful_data_plane_connection_count=self._status.successful_data_plane_connection_count
            + 1,
            data_plane_kind=enrollment.data_plane.kind,
            data_plane_namespace=enrollment.data_plane.namespace,
            data_plane_session_mode=enrollment.data_plane.session_mode,
        )

    async def _admit_transport(
        self, enrollment: GatewayEnrollmentRecord, *, trigger: str
    ) -> bool:
        """Return whether the managed data plane may run for this enrollment.

        Cached certificate files can remain after validation fails. The lifecycle
        result governs admission, including commands from an existing session.
        """
        certificate = await self.certificate_lifecycle_manager.ensure_current(
            enrollment=enrollment,
            trigger=trigger,
        )
        if certificate is not None:
            return True
        lifecycle = self.certificate_lifecycle_manager.current_status()
        detail = lifecycle.detail
        if lifecycle.state is ManagedCertificateState.DISABLED or detail is None:
            detail = "The managed data plane requires a valid client certificate."
        await self._close_transport(enrollment, detail=detail)
        return False

    async def _close_transport(
        self, enrollment: GatewayEnrollmentRecord | None, *, detail: str
    ) -> None:
        await self.close()
        lifecycle = self.certificate_lifecycle_manager.current_status()
        await self._update_status(
            state=UpstreamConnectionState.DISCONNECTED,
            detail=detail,
            last_error=detail,
            client_certificate_present=lifecycle.certificate_present,
            certificate_bootstrap_pending=lifecycle.bootstrap_pending,
            mutual_tls_mode=self._mutual_tls_policy(
                enrollment,
                client_certificate_present=lifecycle.certificate_present,
            ).effective_mode,
        )

    def _admitted_certificate_status(
        self, enrollment: GatewayEnrollmentRecord
    ) -> dict[str, object]:
        return {
            "client_certificate_present": True,
            "certificate_bootstrap_pending": False,
            "mutual_tls_mode": self._mutual_tls_policy(
                enrollment, client_certificate_present=True
            ).effective_mode,
        }

    async def _update_status(self, **changes: object) -> None:
        async with self._lock:
            self._status = replace(self._status, **changes)

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
