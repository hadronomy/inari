from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from ..gateway.models import (
    ControllerAction,
    MutualTlsMode,
    UpstreamCertificateMode,
    UpstreamDataPlaneKind,
    UpstreamEdgeProvider,
    ZenohDataPlaneAuthKind,
    ZenohSerialization,
    ZenohSessionMode,
)
from ..printers import CutMode, PrinterTransport
from ..security.models import GatewayExposure, GatewayMode
from ..version import GATEWAY_PROTOCOL_VERSION, SUPPORTED_GATEWAY_PROTOCOL_VERSIONS


class GatewayProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GatewayMessageType(StrEnum):
    CONTROLLER_SUBMIT_PRINT_JOB = "controller.command.submit_print_job"
    CONTROLLER_EXECUTE_DEVICE_COMMAND = "controller.command.execute_device_command"
    CONTROLLER_CANCEL_JOB = "controller.command.cancel_job"
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
    transport: str = "https+zenoh"
    client_certificate_present: bool = False


class GatewaySecurityDescriptor(GatewayProtocolModel):
    mode: GatewayMode
    exposure: GatewayExposure
    tls_required: bool
    edge_provider: UpstreamEdgeProvider
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


class CertificateTrustPayload(GatewayProtocolModel):
    root_fingerprint: str | None = None


class CertificateBootstrapAuthPayload(GatewayProtocolModel):
    type: Literal["ott"] = "ott"
    token: str | None = None
    expires_at: datetime | None = None


class StepCaCertificateEnrollmentPayload(GatewayProtocolModel):
    base_url: str
    trust: CertificateTrustPayload | None = None
    bootstrap_auth: CertificateBootstrapAuthPayload | None = None
    subject: str | None = None
    authorized_sans: tuple[str, ...] = ()
    requires_mutual_tls_after_issuance: bool = True


class ControllerCertificatePayload(GatewayProtocolModel):
    mode: Literal[UpstreamCertificateMode.CONTROLLER]
    client_certificate_pem: str
    ca_certificate_pem: str | None = None


class StepCaCertificatePayload(GatewayProtocolModel):
    mode: Literal[UpstreamCertificateMode.STEP_CA]
    enrollment: StepCaCertificateEnrollmentPayload


EnrollmentCertificatePayload = Annotated[
    ControllerCertificatePayload | StepCaCertificatePayload,
    Field(discriminator="mode"),
]


class EnrollmentPermissionsPayload(GatewayProtocolModel):
    controller_actions: tuple[ControllerAction, ...] = ()


class EnrollmentDataPlaneAuthPayload(GatewayProtocolModel):
    kind: ZenohDataPlaneAuthKind = ZenohDataPlaneAuthKind.MTLS


class EnrollmentDataPlaneTlsPayload(GatewayProtocolModel):
    close_link_on_expiration: bool = True


class EnrollmentDataPlanePayload(GatewayProtocolModel):
    kind: UpstreamDataPlaneKind = UpstreamDataPlaneKind.ZENOH
    session_mode: ZenohSessionMode = ZenohSessionMode.CLIENT
    connect_endpoints: tuple[str, ...]
    namespace: str
    serialization: ZenohSerialization = ZenohSerialization.JSON
    auth: EnrollmentDataPlaneAuthPayload = Field(
        default_factory=EnrollmentDataPlaneAuthPayload
    )
    tls: EnrollmentDataPlaneTlsPayload = Field(
        default_factory=EnrollmentDataPlaneTlsPayload
    )


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
    permissions: EnrollmentPermissionsPayload = Field(
        default_factory=EnrollmentPermissionsPayload
    )
    data_plane: EnrollmentDataPlanePayload
    certificate: EnrollmentCertificatePayload | None = None
    enrolled_at: datetime


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
    target: GatewayCommandTargetPayload = Field(
        default_factory=GatewayCommandTargetPayload
    )
    options: GatewayPrintOptionsPayload = Field(
        default_factory=GatewayPrintOptionsPayload
    )
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
    target: GatewayCommandTargetPayload = Field(
        default_factory=GatewayCommandTargetPayload
    )
    command: GatewayDeviceCommandPayload
    metadata: dict[str, Any] = Field(default_factory=dict)


class ControllerSubmitPrintJobMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.CONTROLLER_SUBMIT_PRINT_JOB] = (
        GatewayMessageType.CONTROLLER_SUBMIT_PRINT_JOB
    )
    message_id: str
    command_id: str
    sequence: int = Field(ge=1)
    issued_at: datetime | None = None
    payload: ControllerSubmitPrintJobPayload


class ControllerExecuteDeviceCommandMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND] = (
        GatewayMessageType.CONTROLLER_EXECUTE_DEVICE_COMMAND
    )
    message_id: str
    command_id: str
    sequence: int = Field(ge=1)
    issued_at: datetime | None = None
    payload: ControllerExecuteDeviceCommandPayload


class ControllerCancelJobMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.CONTROLLER_CANCEL_JOB] = (
        GatewayMessageType.CONTROLLER_CANCEL_JOB
    )
    message_id: str
    command_id: str
    sequence: int = Field(ge=1)
    issued_at: datetime | None = None
    job_id: str


ControllerCommandMessage = Annotated[
    ControllerSubmitPrintJobMessage
    | ControllerExecuteDeviceCommandMessage
    | ControllerCancelJobMessage,
    Field(discriminator="type"),
]

CONTROLLER_COMMAND_ADAPTER = TypeAdapter(ControllerCommandMessage)
CONTROLLER_COMMAND_LIST_ADAPTER = TypeAdapter(list[ControllerCommandMessage])


class ControllerCommandHistoryPayload(GatewayProtocolModel):
    selected_protocol_version: str = GATEWAY_PROTOCOL_VERSION
    commands: tuple[ControllerCommandMessage, ...] = ()


class AgentCommandAcceptedMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.AGENT_COMMAND_ACCEPTED] = (
        GatewayMessageType.AGENT_COMMAND_ACCEPTED
    )
    message_id: str
    command_id: str
    accepted_at: datetime
    job: dict[str, Any] | None = None
    detail: str


class AgentCommandRejectedMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.AGENT_COMMAND_REJECTED] = (
        GatewayMessageType.AGENT_COMMAND_REJECTED
    )
    message_id: str
    command_id: str
    rejected_at: datetime
    code: str
    detail: str


class AgentRuntimeEventMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.AGENT_RUNTIME_EVENT] = (
        GatewayMessageType.AGENT_RUNTIME_EVENT
    )
    message_id: str
    occurred_at: datetime
    event: GatewayRuntimeEventPayload
    command_id: str | None = None
    job_id: str | None = None


class AgentStatusSnapshotMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.AGENT_STATUS_SNAPSHOT] = (
        GatewayMessageType.AGENT_STATUS_SNAPSHOT
    )
    message_id: str
    snapshot: GatewaySnapshotPayload


class AgentErrorMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.AGENT_ERROR] = GatewayMessageType.AGENT_ERROR
    message_id: str
    occurred_at: datetime
    code: str
    detail: str
    command_id: str | None = None
    retriable: bool = False


AgentPublicationMessage = Annotated[
    AgentCommandAcceptedMessage
    | AgentCommandRejectedMessage
    | AgentRuntimeEventMessage
    | AgentStatusSnapshotMessage
    | AgentErrorMessage,
    Field(discriminator="type"),
]

AGENT_PUBLICATION_ADAPTER = TypeAdapter(AgentPublicationMessage)
