from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .models import (
    MutualTlsMode,
    UpstreamAuthMode,
    UpstreamCertificateMode,
    UpstreamEdgeProvider,
)
from ..security.models import AccessScope, GatewayExposure, GatewayMode
from ..version import GATEWAY_PROTOCOL_VERSION, SUPPORTED_GATEWAY_PROTOCOL_VERSIONS


class GatewayProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UpstreamControlMessageType(StrEnum):
    CONTROLLER_HELLO = "controller.hello"
    CONTROLLER_ACK = "controller.ack"
    CONTROLLER_PING = "controller.ping"
    CONTROLLER_SUBMIT_PRINT_JOB = "controller.command.submit_print_job"
    CONTROLLER_EXECUTE_DEVICE_COMMAND = "controller.command.execute_device_command"
    CONTROLLER_CANCEL_JOB = "controller.command.cancel_job"
    AGENT_HELLO = "agent.hello"
    AGENT_ACK = "agent.ack"
    AGENT_PONG = "agent.pong"
    AGENT_COMMAND_ACCEPTED = "agent.command.accepted"
    AGENT_COMMAND_REJECTED = "agent.command.rejected"
    AGENT_RUNTIME_EVENT = "agent.runtime.event"
    AGENT_STATUS_SNAPSHOT = "agent.status.snapshot"
    AGENT_ERROR = "agent.error"


class GatewayProtocolDescriptor(GatewayProtocolModel):
    version: str = GATEWAY_PROTOCOL_VERSION
    supported_versions: tuple[str, ...] = SUPPORTED_GATEWAY_PROTOCOL_VERSIONS


class GatewayCapabilityDescriptor(GatewayProtocolModel):
    supported_content_kinds: tuple[str, ...]
    supported_device_commands: tuple[str, ...]
    granted_scopes: tuple[AccessScope, ...]
    features: tuple[str, ...]
    transport: str = "https+wss"
    client_certificate_present: bool = False


class GatewaySecurityDescriptor(GatewayProtocolModel):
    mode: GatewayMode
    exposure: GatewayExposure
    tls_required: bool
    edge_provider: UpstreamEdgeProvider
    auth_mode: UpstreamAuthMode
    certificate_mode: UpstreamCertificateMode
    mutual_tls_mode: MutualTlsMode
    mutual_tls_enabled: bool
    certificate_expires_at: datetime | None = None


class GatewayDeviceSummary(GatewayProtocolModel):
    count: int
    online_count: int
    offline_count: int
    kind_counts: dict[str, int]
    default_device_id: str | None = None
    default_device_name: str | None = None


class GatewayQueueSummary(GatewayProtocolModel):
    total: int
    queued: int = 0
    dispatched: int = 0
    running: int = 0
    retry_scheduled: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0


class GatewayRuntimeSummary(GatewayProtocolModel):
    queue: GatewayQueueSummary
    devices: GatewayDeviceSummary


class GatewayRuntimeEventPayload(GatewayProtocolModel):
    sequence: int
    resource_kind: str
    resource_id: str
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class GatewayControllerDescriptor(GatewayProtocolModel):
    name: str | None = None
    instance_id: str | None = None
    protocol_version: str | None = None


class GatewaySnapshotPayload(GatewayProtocolModel):
    generated_at: datetime
    protocol: GatewayProtocolDescriptor
    service: dict[str, Any]
    security: GatewaySecurityDescriptor
    runtime: GatewayRuntimeSummary
    capabilities: GatewayCapabilityDescriptor
    observability: dict[str, Any] = Field(default_factory=dict)


class CertificateBootstrapPayload(GatewayProtocolModel):
    mode: Literal["step_ca_ott"] = "step_ca_ott"
    ca_url: str
    root_fingerprint: str
    ott: str | None = None
    sign_url: str | None = None
    renew_url: str | None = None
    expires_at: datetime | None = None
    subject: str | None = None
    authorized_sans: tuple[str, ...] = ()
    requires_mutual_tls_after_issuance: bool = False


class EnrollmentRequestPayload(GatewayProtocolModel):
    protocol: GatewayProtocolDescriptor = Field(
        default_factory=GatewayProtocolDescriptor
    )
    agent_id: str
    key_id: str
    public_jwk: dict[str, Any]
    certificate_pem: str | None = None
    csr_pem: str
    snapshot: GatewaySnapshotPayload


class EnrollmentResponsePayload(GatewayProtocolModel):
    protocol_version: str = GATEWAY_PROTOCOL_VERSION
    controller_name: str | None = None
    controller_instance_id: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    enrolled_at: datetime
    expires_at: datetime | None = None
    refresh_url: str | None = None
    status_url: str | None = None
    events_url: str | None = None
    granted_scopes: tuple[AccessScope, ...] = ()
    certificate_pem: str | None = None
    ca_certificate_pem: str | None = None
    certificate_bootstrap: CertificateBootstrapPayload | None = None


class ControllerHelloMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_HELLO] = (
        UpstreamControlMessageType.CONTROLLER_HELLO
    )
    message_id: str
    protocol_version: str
    controller_name: str | None = None
    controller_instance_id: str | None = None


class ControllerAcknowledgeMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_ACK] = (
        UpstreamControlMessageType.CONTROLLER_ACK
    )
    message_id: str
    acknowledged_message_id: str


class ControllerPingMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_PING] = (
        UpstreamControlMessageType.CONTROLLER_PING
    )
    message_id: str
    detail: str | None = None


class ControllerSubmitPrintJobMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_SUBMIT_PRINT_JOB] = (
        UpstreamControlMessageType.CONTROLLER_SUBMIT_PRINT_JOB
    )
    message_id: str
    command_id: str
    issued_at: datetime | None = None
    payload: dict[str, Any]


class ControllerExecuteDeviceCommandMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND] = (
        UpstreamControlMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND
    )
    message_id: str
    command_id: str
    issued_at: datetime | None = None
    payload: dict[str, Any]


class ControllerCancelJobMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_CANCEL_JOB] = (
        UpstreamControlMessageType.CONTROLLER_CANCEL_JOB
    )
    message_id: str
    command_id: str
    issued_at: datetime | None = None
    job_id: str


ControllerStreamMessage = Annotated[
    ControllerHelloMessage
    | ControllerAcknowledgeMessage
    | ControllerPingMessage
    | ControllerSubmitPrintJobMessage
    | ControllerExecuteDeviceCommandMessage
    | ControllerCancelJobMessage,
    Field(discriminator="type"),
]

CONTROLLER_STREAM_ADAPTER = TypeAdapter(ControllerStreamMessage)


class AgentHelloMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_HELLO] = (
        UpstreamControlMessageType.AGENT_HELLO
    )
    message_id: str
    protocol: GatewayProtocolDescriptor = Field(
        default_factory=GatewayProtocolDescriptor
    )
    snapshot: GatewaySnapshotPayload


class AgentAcknowledgeMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_ACK] = (
        UpstreamControlMessageType.AGENT_ACK
    )
    message_id: str
    acknowledged_message_id: str


class AgentPongMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_PONG] = (
        UpstreamControlMessageType.AGENT_PONG
    )
    message_id: str
    acknowledged_message_id: str
    detail: str | None = None


class AgentCommandAcceptedMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_COMMAND_ACCEPTED] = (
        UpstreamControlMessageType.AGENT_COMMAND_ACCEPTED
    )
    message_id: str
    command_id: str
    accepted_at: datetime
    job: dict[str, Any] | None = None
    detail: str


class AgentCommandRejectedMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_COMMAND_REJECTED] = (
        UpstreamControlMessageType.AGENT_COMMAND_REJECTED
    )
    message_id: str
    command_id: str
    rejected_at: datetime
    code: str
    detail: str


class AgentRuntimeEventMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_RUNTIME_EVENT] = (
        UpstreamControlMessageType.AGENT_RUNTIME_EVENT
    )
    message_id: str
    occurred_at: datetime
    event: GatewayRuntimeEventPayload
    command_id: str | None = None
    job_id: str | None = None


class AgentStatusSnapshotMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_STATUS_SNAPSHOT] = (
        UpstreamControlMessageType.AGENT_STATUS_SNAPSHOT
    )
    message_id: str
    snapshot: GatewaySnapshotPayload


class AgentErrorMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.AGENT_ERROR] = (
        UpstreamControlMessageType.AGENT_ERROR
    )
    message_id: str
    occurred_at: datetime
    code: str
    detail: str
    command_id: str | None = None
    retriable: bool = False


AgentStreamMessage = Annotated[
    AgentHelloMessage
    | AgentAcknowledgeMessage
    | AgentPongMessage
    | AgentCommandAcceptedMessage
    | AgentCommandRejectedMessage
    | AgentRuntimeEventMessage
    | AgentStatusSnapshotMessage
    | AgentErrorMessage,
    Field(discriminator="type"),
]
