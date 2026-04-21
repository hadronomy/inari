from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ...core.exceptions import AgentError
from ...local_api.schemas import (
    DeviceCommandRequest,
    JobResponse,
    PrintJobRequest,
    RuntimeEventResponse,
)
from ...runtime.events import EventHub
from ...runtime.jobs.service import JobService
from ..models import (
    ControllerAction,
    GatewayEnrollmentRecord,
    GatewayInboundCommandState,
)
from ..protocol import (
    AgentCommandAcceptedMessage,
    AgentCommandRejectedMessage,
    ControllerExecuteDeviceCommandPayload,
    AgentRuntimeEventMessage,
    ControllerCancelJobMessage,
    ControllerExecuteDeviceCommandMessage,
    ControllerSubmitPrintJobPayload,
    ControllerSubmitPrintJobMessage,
)
from ..repositories import GatewayRepository


class GatewayCommandDispatcher:
    def __init__(
        self,
        *,
        job_service: JobService,
        gateway_repository: GatewayRepository,
    ) -> None:
        self.job_service = job_service
        self.gateway_repository = gateway_repository

    async def handle_submit_print_job(
        self,
        message: ControllerSubmitPrintJobMessage,
        *,
        enrollment: GatewayEnrollmentRecord,
    ) -> None:
        await self._handle_job_submission(
            message=message,
            enrollment=enrollment,
            required_action=ControllerAction.JOBS_CREATE,
            enqueue=lambda: self.job_service.enqueue_print(
                _protocol_print_job_request(message.payload).to_operation()
            ),
        )

    async def handle_execute_device_command(
        self,
        message: ControllerExecuteDeviceCommandMessage,
        *,
        enrollment: GatewayEnrollmentRecord,
    ) -> None:
        await self._handle_job_submission(
            message=message,
            enrollment=enrollment,
            required_action=ControllerAction.COMMANDS_EXECUTE,
            enqueue=lambda: self.job_service.enqueue_command(
                _protocol_device_command_request(message.payload).to_operation()
            ),
        )

    async def handle_cancel_job(
        self,
        message: ControllerCancelJobMessage,
        *,
        enrollment: GatewayEnrollmentRecord,
    ) -> None:
        self._require_action(enrollment, ControllerAction.JOBS_CANCEL)
        record, created = self.gateway_repository.record_inbound_command(
            command_id=message.command_id,
            message_id=message.message_id,
            sequence=message.sequence,
            message_type=message.type,
            payload=message.model_dump(mode="json"),
        )
        if not created:
            self._replay_record(record)
            return
        try:
            job = await self.job_service.cancel(message.job_id)
        except Exception as exc:
            self._reject_command(message.command_id, exc)
            return
        accepted = AgentCommandAcceptedMessage(
            message_id=_message_id("gack"),
            command_id=message.command_id,
            accepted_at=_utc_now(),
            job=JobResponse.from_domain(job).model_dump(mode="json"),
            detail=f"Cancellation request accepted for job {job.id}.",
        )
        self.gateway_repository.mark_inbound_accepted(
            message.command_id,
            job_id=job.id,
            response_payload=accepted.model_dump(mode="json"),
        )
        self.gateway_repository.enqueue_outbound(
            message_type=accepted.type,
            payload=accepted.model_dump(mode="json"),
            correlation_id=message.command_id,
            dedupe_key=f"command-accepted:{message.command_id}",
        )

    async def _handle_job_submission(
        self,
        *,
        message: ControllerSubmitPrintJobMessage
        | ControllerExecuteDeviceCommandMessage,
        enrollment: GatewayEnrollmentRecord,
        required_action: ControllerAction,
        enqueue,
    ) -> None:
        self._require_action(enrollment, required_action)
        record, created = self.gateway_repository.record_inbound_command(
            command_id=message.command_id,
            message_id=message.message_id,
            sequence=message.sequence,
            message_type=message.type,
            payload=message.model_dump(mode="json"),
        )
        if not created:
            self._replay_record(record)
            return

        try:
            job = await enqueue()
        except Exception as exc:
            self._reject_command(message.command_id, exc)
            return

        accepted = AgentCommandAcceptedMessage(
            message_id=_message_id("gack"),
            command_id=message.command_id,
            accepted_at=_utc_now(),
            job=JobResponse.from_domain(job).model_dump(mode="json"),
            detail=f"Accepted upstream command and queued job {job.id}.",
        )
        self.gateway_repository.mark_inbound_accepted(
            message.command_id,
            job_id=job.id,
            response_payload=accepted.model_dump(mode="json"),
        )
        self.gateway_repository.enqueue_outbound(
            message_type=accepted.type,
            payload=accepted.model_dump(mode="json"),
            correlation_id=message.command_id,
            dedupe_key=f"command-accepted:{message.command_id}",
        )

    def _require_action(
        self, enrollment: GatewayEnrollmentRecord, action: ControllerAction
    ) -> None:
        permitted = set(enrollment.controller_actions)
        if action not in permitted:
            raise AgentError(
                "UPSTREAM_SCOPE_DENIED",
                f"The upstream controller is not authorized for action {action.value!r}.",
                status_code=403,
            )

    def _replay_record(self, record) -> None:
        if record.response_payload is None:
            return
        self.gateway_repository.enqueue_outbound(
            message_type=str(
                record.response_payload.get("type", "agent.command.rejected")
            ),
            payload=record.response_payload,
            correlation_id=record.command_id,
        )

    def _reject_command(self, command_id: str, exc: Exception) -> None:
        error = _coerce_error(exc)
        rejected = AgentCommandRejectedMessage(
            message_id=_message_id("gerr"),
            command_id=command_id,
            rejected_at=_utc_now(),
            code=error.code,
            detail=error.message,
        )
        self.gateway_repository.mark_inbound_rejected(
            command_id,
            error_code=error.code,
            error_detail=error.message,
            response_payload=rejected.model_dump(mode="json"),
        )
        self.gateway_repository.enqueue_outbound(
            message_type=rejected.type,
            payload=rejected.model_dump(mode="json"),
            correlation_id=command_id,
            dedupe_key=f"command-rejected:{command_id}",
        )


class GatewayRuntimeEventForwarder:
    def __init__(
        self,
        *,
        event_hub: EventHub,
        gateway_repository: GatewayRepository,
    ) -> None:
        self.event_hub = event_hub
        self.gateway_repository = gateway_repository

    async def run_forever(self) -> None:
        async for event in self.event_hub.iter_events():
            command_id = None
            if event.resource_kind == "job":
                inbound = self.gateway_repository.get_inbound_command_for_job(
                    event.resource_id
                )
                if (
                    inbound is not None
                    and inbound.state is GatewayInboundCommandState.ACCEPTED
                ):
                    command_id = inbound.command_id
            message = AgentRuntimeEventMessage(
                message_id=_message_id("gevt"),
                occurred_at=event.occurred_at,
                event=RuntimeEventResponse.from_domain(event).model_dump(mode="json"),
                command_id=command_id,
                job_id=event.resource_id if event.resource_kind == "job" else None,
            )
            self.gateway_repository.enqueue_outbound(
                message_type=message.type,
                payload=message.model_dump(mode="json"),
                correlation_id=command_id,
                dedupe_key=f"runtime-event:{event.sequence}",
            )


def _coerce_error(exc: Exception) -> AgentError:
    if isinstance(exc, AgentError):
        return exc
    return AgentError(
        "UPSTREAM_COMMAND_FAILED",
        f"Upstream command failed with {type(exc).__name__}.",
        status_code=500,
        details={"cause": type(exc).__name__},
    )


def _message_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _protocol_print_job_request(
    payload: ControllerSubmitPrintJobPayload,
) -> PrintJobRequest:
    return PrintJobRequest.model_validate(
        {
            "content": payload.content.model_dump(mode="json"),
            "target": {
                "device_id": payload.target.device_id,
                "printer_name": payload.target.printer_name,
            },
            "options": payload.options.model_dump(mode="json"),
            "metadata": dict(payload.metadata),
        }
    )


def _protocol_device_command_request(
    payload: ControllerExecuteDeviceCommandPayload,
) -> DeviceCommandRequest:
    return DeviceCommandRequest.model_validate(
        {
            "target": {
                "device_id": payload.target.device_id,
                "printer_name": payload.target.printer_name,
            },
            "command": payload.command.model_dump(mode="json"),
            "metadata": dict(payload.metadata),
        }
    )
