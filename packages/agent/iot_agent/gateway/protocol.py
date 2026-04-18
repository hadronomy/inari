from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from ..gateway.models import (
    ControllerAction,
    MutualTlsMode,
    UpstreamAuthMode,
    UpstreamCertificateMode,
    UpstreamEdgeProvider,
    parse_controller_actions,
)
from ..printers import CutMode, PrinterTransport
from ..security.models import GatewayExposure, GatewayMode
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
    supported_controller_actions: tuple[ControllerAction, ...]
    features: tuple[str, ...]
    transport: str = "https+wss"
    client_certificate_present: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "supported_controller_actions" in value:
            return value
        normalized = dict(value)
        normalized["supported_controller_actions"] = list(
            parse_controller_actions(normalized.get("granted_scopes"))
        )
        normalized.pop("granted_scopes", None)
        return normalized


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


class GatewayControllerInfo(GatewayProtocolModel):
    name: str | None = None
    instance_id: str | None = None


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
    requires_mutual_tls_after_issuance: bool = True


class EnrollmentCertificatePayload(GatewayProtocolModel):
    mode: UpstreamCertificateMode
    client_certificate_pem: str | None = None
    ca_certificate_pem: str | None = None
    bootstrap: CertificateBootstrapPayload | None = None


class EnrollmentLinksPayload(GatewayProtocolModel):
    refresh: str | None = None
    status: str | None = None
    events: str | None = None


class EnrollmentPermissionsPayload(GatewayProtocolModel):
    controller_actions: tuple[ControllerAction, ...] = ()


class ControllerManagedAuthPayload(GatewayProtocolModel):
    mode: Literal["controller"] = "controller"
    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_at: datetime | None = None


class ZitadelServiceAccountAuthPayload(GatewayProtocolModel):
    mode: Literal["zitadel_service_account"] = "zitadel_service_account"
    issuer: str | None = None
    token_endpoint: str | None = None
    audience: str | None = None


EnrollmentAuthPayload = Annotated[
    ControllerManagedAuthPayload | ZitadelServiceAccountAuthPayload,
    Field(discriminator="mode"),
]


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
    selected_protocol_version: str = GATEWAY_PROTOCOL_VERSION
    controller: GatewayControllerInfo | None = None
    auth: EnrollmentAuthPayload
    links: EnrollmentLinksPayload = Field(default_factory=EnrollmentLinksPayload)
    permissions: EnrollmentPermissionsPayload = Field(
        default_factory=EnrollmentPermissionsPayload
    )
    certificate: EnrollmentCertificatePayload | None = None
    enrolled_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "auth" in value or "links" in value or "controller" in value:
            return value

        normalized = dict(value)
        auth_mode = str(
            normalized.get("auth_mode")
            or (
                UpstreamAuthMode.CONTROLLER.value
                if normalized.get("access_token") or normalized.get("refresh_token")
                else UpstreamAuthMode.ZITADEL_SERVICE_ACCOUNT.value
            )
        )
        if auth_mode == UpstreamAuthMode.CONTROLLER.value:
            normalized["auth"] = {
                "mode": auth_mode,
                "access_token": normalized.get("access_token"),
                "refresh_token": normalized.get("refresh_token"),
                "token_type": normalized.get("token_type") or "Bearer",
                "expires_at": normalized.get("expires_at"),
            }
        else:
            normalized["auth"] = {
                "mode": auth_mode,
                "issuer": normalized.get("auth_issuer"),
                "token_endpoint": normalized.get("auth_token_endpoint"),
                "audience": normalized.get("auth_audience"),
            }
        normalized["selected_protocol_version"] = (
            normalized.get("selected_protocol_version")
            or normalized.get("protocol_version")
            or GATEWAY_PROTOCOL_VERSION
        )
        normalized["controller"] = {
            "name": normalized.get("controller_name"),
            "instance_id": normalized.get("controller_instance_id"),
        }
        normalized["links"] = {
            "refresh": normalized.get("refresh_url"),
            "status": normalized.get("status_url"),
            "events": normalized.get("events_url"),
        }
        normalized["permissions"] = {
            "controller_actions": list(
                parse_controller_actions(normalized.get("granted_scopes"))
            )
        }
        certificate_mode = normalized.get("certificate_mode")
        has_certificate_fields = any(
            normalized.get(field)
            for field in ("certificate_pem", "ca_certificate_pem", "certificate_bootstrap")
        )
        if certificate_mode or has_certificate_fields:
            normalized["certificate"] = {
                "mode": certificate_mode
                or (
                    UpstreamCertificateMode.STEP_CA.value
                    if normalized.get("certificate_bootstrap")
                    else UpstreamCertificateMode.CONTROLLER.value
                ),
                "client_certificate_pem": normalized.get("certificate_pem"),
                "ca_certificate_pem": normalized.get("ca_certificate_pem"),
                "bootstrap": normalized.get("certificate_bootstrap"),
            }
        for legacy_key in (
            "auth_mode",
            "access_token",
            "refresh_token",
            "token_type",
            "expires_at",
            "protocol_version",
            "controller_name",
            "controller_instance_id",
            "refresh_url",
            "status_url",
            "events_url",
            "granted_scopes",
            "certificate_mode",
            "certificate_pem",
            "ca_certificate_pem",
            "certificate_bootstrap",
            "auth_issuer",
            "auth_token_endpoint",
            "auth_audience",
        ):
            normalized.pop(legacy_key, None)
        return normalized


class RefreshRequestPayload(GatewayProtocolModel):
    selected_protocol_version: str = GATEWAY_PROTOCOL_VERSION
    agent_id: str


class RefreshResponsePayload(GatewayProtocolModel):
    selected_protocol_version: str = GATEWAY_PROTOCOL_VERSION
    controller: GatewayControllerInfo | None = None
    auth: EnrollmentAuthPayload
    links: EnrollmentLinksPayload = Field(default_factory=EnrollmentLinksPayload)

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "auth" in value or "controller" in value:
            return value
        normalized = dict(value)
        auth_mode = str(
            normalized.get("auth_mode")
            or (
                UpstreamAuthMode.CONTROLLER.value
                if normalized.get("access_token") or normalized.get("refresh_token")
                else UpstreamAuthMode.ZITADEL_SERVICE_ACCOUNT.value
            )
        )
        if auth_mode == UpstreamAuthMode.CONTROLLER.value:
            normalized["auth"] = {
                "mode": auth_mode,
                "access_token": normalized.get("access_token"),
                "refresh_token": normalized.get("refresh_token"),
                "token_type": normalized.get("token_type") or "Bearer",
                "expires_at": normalized.get("expires_at"),
            }
        else:
            normalized["auth"] = {
                "mode": auth_mode,
                "issuer": normalized.get("auth_issuer"),
                "token_endpoint": normalized.get("auth_token_endpoint"),
                "audience": normalized.get("auth_audience"),
            }
        normalized["selected_protocol_version"] = (
            normalized.get("selected_protocol_version")
            or normalized.get("protocol_version")
            or GATEWAY_PROTOCOL_VERSION
        )
        normalized["controller"] = {
            "name": normalized.get("controller_name"),
            "instance_id": normalized.get("controller_instance_id"),
        }
        normalized["links"] = {
            "refresh": normalized.get("refresh_url"),
            "status": normalized.get("status_url"),
            "events": normalized.get("events_url"),
        }
        for legacy_key in (
            "auth_mode",
            "access_token",
            "refresh_token",
            "token_type",
            "expires_at",
            "protocol_version",
            "controller_name",
            "controller_instance_id",
            "refresh_url",
            "status_url",
            "events_url",
            "auth_issuer",
            "auth_token_endpoint",
            "auth_audience",
        ):
            normalized.pop(legacy_key, None)
        return normalized


class ControllerHelloMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_HELLO] = (
        UpstreamControlMessageType.CONTROLLER_HELLO
    )
    message_id: str
    selected_protocol_version: str
    resume_from_sequence: int | None = None
    controller: GatewayControllerInfo | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if "selected_protocol_version" in value or "controller" in value:
            return value
        normalized = dict(value)
        normalized["selected_protocol_version"] = normalized.get("protocol_version")
        normalized["controller"] = {
            "name": normalized.get("controller_name"),
            "instance_id": normalized.get("controller_instance_id"),
        }
        normalized.pop("protocol_version", None)
        normalized.pop("controller_name", None)
        normalized.pop("controller_instance_id", None)
        return normalized


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


class GatewayCommandTargetPayload(GatewayProtocolModel):
    device_id: str | None = None
    printer_name: str | None = None


class GatewayPrintOptionsPayload(GatewayProtocolModel):
    transport: PrinterTransport = PrinterTransport.AUTO
    open_cash_drawer: bool = False


class GatewayBinaryContentPayload(GatewayProtocolModel):
    base64: str
    declared_mime_type: str | None = None


class GatewayStructuredReceiptContentPayload(GatewayProtocolModel):
    kind: Literal["structured_receipt"] = "structured_receipt"
    data: dict[str, Any]
    document_name: str = "Receipt"


class GatewayReceiptImageContentPayload(GatewayProtocolModel):
    kind: Literal["receipt_image"] = "receipt_image"
    binary: GatewayBinaryContentPayload
    document_name: str = "Receipt"


class GatewayTextDocumentContentPayload(GatewayProtocolModel):
    kind: Literal["text"] = "text"
    text: str
    document_name: str = "Text Document"


class GatewayHtmlDocumentContentPayload(GatewayProtocolModel):
    kind: Literal["html"] = "html"
    html: str
    document_name: str = "HTML Document"


class GatewayPdfDocumentContentPayload(GatewayProtocolModel):
    kind: Literal["pdf"] = "pdf"
    binary: GatewayBinaryContentPayload
    document_name: str = "PDF Document"


class GatewayRawDocumentContentPayload(GatewayProtocolModel):
    kind: Literal["raw"] = "raw"
    binary: GatewayBinaryContentPayload
    data_type: str = "RAW"
    document_name: str = "Raw Document"


GatewayPrintContentPayload = Annotated[
    GatewayStructuredReceiptContentPayload
    | GatewayReceiptImageContentPayload
    | GatewayTextDocumentContentPayload
    | GatewayHtmlDocumentContentPayload
    | GatewayPdfDocumentContentPayload
    | GatewayRawDocumentContentPayload,
    Field(discriminator="kind"),
]


class ControllerSubmitPrintJobPayload(GatewayProtocolModel):
    content: GatewayPrintContentPayload
    target: GatewayCommandTargetPayload = Field(default_factory=GatewayCommandTargetPayload)
    options: GatewayPrintOptionsPayload = Field(default_factory=GatewayPrintOptionsPayload)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayOpenCashDrawerCommandPayload(GatewayProtocolModel):
    kind: Literal["open_cash_drawer"] = "open_cash_drawer"


class GatewayPrintTestPageCommandPayload(GatewayProtocolModel):
    kind: Literal["print_test_page"] = "print_test_page"
    transport: PrinterTransport = PrinterTransport.AUTO


class GatewayFeedLinesCommandPayload(GatewayProtocolModel):
    kind: Literal["feed_lines"] = "feed_lines"
    count: int = Field(gt=0, le=24)


class GatewayFeedDotsCommandPayload(GatewayProtocolModel):
    kind: Literal["feed_dots"] = "feed_dots"
    count: int = Field(gt=0, le=255)


class GatewayCutPaperCommandPayload(GatewayProtocolModel):
    kind: Literal["cut_paper"] = "cut_paper"
    mode: CutMode = CutMode.PARTIAL


GatewayDeviceCommandPayload = Annotated[
    GatewayOpenCashDrawerCommandPayload
    | GatewayPrintTestPageCommandPayload
    | GatewayFeedLinesCommandPayload
    | GatewayFeedDotsCommandPayload
    | GatewayCutPaperCommandPayload,
    Field(discriminator="kind"),
]


class ControllerExecuteDeviceCommandPayload(GatewayProtocolModel):
    target: GatewayCommandTargetPayload = Field(default_factory=GatewayCommandTargetPayload)
    command: GatewayDeviceCommandPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControllerSubmitPrintJobMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_SUBMIT_PRINT_JOB] = (
        UpstreamControlMessageType.CONTROLLER_SUBMIT_PRINT_JOB
    )
    message_id: str
    command_id: str
    sequence: int | None = Field(default=None, ge=1)
    issued_at: datetime | None = None
    payload: ControllerSubmitPrintJobPayload


class ControllerExecuteDeviceCommandMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND] = (
        UpstreamControlMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND
    )
    message_id: str
    command_id: str
    sequence: int | None = Field(default=None, ge=1)
    issued_at: datetime | None = None
    payload: ControllerExecuteDeviceCommandPayload


class ControllerCancelJobMessage(GatewayProtocolModel):
    type: Literal[UpstreamControlMessageType.CONTROLLER_CANCEL_JOB] = (
        UpstreamControlMessageType.CONTROLLER_CANCEL_JOB
    )
    message_id: str
    command_id: str
    sequence: int | None = Field(default=None, ge=1)
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
    selected_protocol_version: str = GATEWAY_PROTOCOL_VERSION
    supported_versions: tuple[str, ...] = SUPPORTED_GATEWAY_PROTOCOL_VERSIONS
    last_applied_controller_sequence: int | None = None
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

AGENT_STREAM_ADAPTER = TypeAdapter(AgentStreamMessage)
