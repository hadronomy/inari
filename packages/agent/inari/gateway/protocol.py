from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    StringConstraints,
    field_serializer,
)

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
from ..printing.protocols import CutMode
from ..security.models import GatewayExposure, GatewayMode
from ..core.version import GATEWAY_PROTOCOL_VERSION, SUPPORTED_GATEWAY_PROTOCOL_VERSIONS


class GatewayProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


type JsonObject = dict[str, JsonValue]
JSON_OBJECT_ADAPTER = TypeAdapter(JsonObject)


class GatewayMessageType(StrEnum):
    CONTROLLER_EXECUTE_DEVICE_COMMAND = "controller.command.execute_device_command"
    CONTROLLER_CANCEL_JOB = "controller.command.cancel_job"
    CONTROLLER_DISPATCH_DEVICE_WORK = "controller.command.dispatch_device_work"
    AGENT_COMMAND_ACCEPTED = "agent.command.accepted"
    AGENT_COMMAND_REJECTED = "agent.command.rejected"
    AGENT_RUNTIME_EVENT = "agent.runtime.event"
    AGENT_STATUS_SNAPSHOT = "agent.status.snapshot"
    AGENT_ERROR = "agent.error"


class GatewayProtocolDescriptor(GatewayProtocolModel):
    version: str = GATEWAY_PROTOCOL_VERSION
    supported_versions: tuple[str, ...] = SUPPORTED_GATEWAY_PROTOCOL_VERSIONS


class GatewayCapabilityDescriptor(GatewayProtocolModel):
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


class GatewayServiceDescriptor(GatewayProtocolModel):
    name: str | None = None
    version: str | None = None
    agent_id: str | None = None
    key_id: str | None = None


class GatewayDeviceSummary(GatewayProtocolModel):
    count: int
    online_count: int
    offline_count: int
    kind_counts: dict[str, int]
    default_device_id: str | None = None
    default_device_name: str | None = None


class GatewayDeviceInventoryItem(GatewayProtocolModel):
    device_id: str
    kind: str
    device_class: str
    display_name: str
    system_name: str
    driver_key: str
    connection_state: str
    capabilities: tuple[str, ...] = ()
    metadata: JsonObject = Field(default_factory=dict)


class GatewayDeviceInventory(GatewayProtocolModel):
    devices: tuple[GatewayDeviceInventoryItem, ...] = ()


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
    inventory: GatewayDeviceInventory = Field(default_factory=GatewayDeviceInventory)


class GatewayRuntimeEventPayload(GatewayProtocolModel):
    sequence: int
    resource_kind: str
    resource_id: str
    event_type: str
    occurred_at: datetime
    payload: JsonObject = Field(default_factory=dict)


class GatewayControllerInfo(GatewayProtocolModel):
    name: str | None = None
    instance_id: str | None = None


class GatewaySnapshotPayload(GatewayProtocolModel):
    generated_at: datetime
    protocol: GatewayProtocolDescriptor
    service: GatewayServiceDescriptor
    security: GatewaySecurityDescriptor
    runtime: GatewayRuntimeSummary
    capabilities: GatewayCapabilityDescriptor
    observability: JsonObject = Field(default_factory=dict)


class Ed25519PublicJwk(GatewayProtocolModel):
    kty: Literal["OKP"] = "OKP"
    crv: Literal["Ed25519"] = "Ed25519"
    x: str
    kid: str
    alg: Literal["EdDSA"] = "EdDSA"
    use: Literal["sig"] = "sig"


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


class DispatchEncryptionKeyPayload(GatewayProtocolModel):
    key_id: str = Field(min_length=1, max_length=256)
    kem: Literal["dhkem_x25519_hkdf_sha256"] = "dhkem_x25519_hkdf_sha256"
    public_key_base64url: str = Field(min_length=43, max_length=43)


class AgentManagedScopePayload(GatewayProtocolModel):
    organization_id: str = Field(min_length=1, max_length=256)
    site_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=256)


class ManagedDispatchEnrollmentPayload(GatewayProtocolModel):
    scope: AgentManagedScopePayload
    issuer: str = Field(min_length=1, max_length=256)
    epoch: int = Field(ge=1)
    verification_jwk: Ed25519PublicJwk


class EnrollmentRequestPayload(GatewayProtocolModel):
    protocol: GatewayProtocolDescriptor = Field(
        default_factory=GatewayProtocolDescriptor
    )
    agent_id: str
    key_id: str
    public_jwk: Ed25519PublicJwk
    dispatch_key: DispatchEncryptionKeyPayload
    state_signing_jwk: Ed25519PublicJwk
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
    managed_dispatch: ManagedDispatchEnrollmentPayload | None = None
    enrolled_at: datetime


class ManagedWorkScopePayload(GatewayProtocolModel):
    database: str = Field(min_length=1, max_length=256)
    company_id: str = Field(min_length=1, max_length=256)
    organization_id: str = Field(min_length=1, max_length=256)
    site_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=256)


class ReportBindingClaimPayload(GatewayProtocolModel):
    report_binding_id: str = Field(min_length=1, max_length=256)
    binding_revision_id: str = Field(min_length=1, max_length=256)
    report_action_id: str = Field(min_length=1, max_length=256)
    report_contract_digest: str = Field(min_length=1, max_length=256)
    template_digest: str = Field(min_length=1, max_length=256)
    command_profile_id: str | None = None
    layout_profile_id: str | None = None
    hardware_matrix_digest: str | None = None


class RecordsReportSourcePayload(GatewayProtocolModel):
    kind: Literal["records"] = "records"
    model: str = Field(min_length=1, max_length=256)
    ordered_ids: tuple[int, ...]


class WizardReportSourcePayload(GatewayProtocolModel):
    kind: Literal["wizard"] = "wizard"
    model: str = Field(min_length=1, max_length=256)
    input_digest: str = Field(min_length=1, max_length=256)


ReportSourcePayload = Annotated[
    RecordsReportSourcePayload | WizardReportSourcePayload,
    Field(discriminator="kind"),
]


class ReportPrintOriginPayload(GatewayProtocolModel):
    binding: ReportBindingClaimPayload
    route: Literal["manual", "automatic"]
    source: ReportSourcePayload
    rendered_document_index: int = Field(ge=0)
    copy_ordinal: int = Field(ge=1)


class ReportPdfPayload(GatewayProtocolModel):
    operation: Literal["report_pdf"] = "report_pdf"
    content_base64: str


class LabelDocumentPayload(GatewayProtocolModel):
    operation: Literal["label_document"] = "label_document"
    content_base64: str


ManagedDocumentPayload = Annotated[
    ReportPdfPayload | LabelDocumentPayload,
    Field(discriminator="operation"),
]


class ManagedDeviceWorkPayload(GatewayProtocolModel):
    contract_major: Literal[1] = 1
    scope: ManagedWorkScopePayload
    print_intent_id: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=256)
    origin: ReportPrintOriginPayload
    document: ManagedDocumentPayload
    normalized_device_options: JsonObject = Field(default_factory=dict)


class ManagedDispatchAuthenticatedDataPayload(GatewayProtocolModel):
    organization_id: str = Field(min_length=1, max_length=256)
    site_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=256)
    managed_work_id: str = Field(min_length=1, max_length=256)
    idempotency_key: Annotated[
        str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)
    ]
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dispatch_epoch: int = Field(ge=1)
    sequence: int = Field(ge=1)
    issued_at: int
    expires_at: int
    work_expires_at: AwareDatetime

    @field_serializer("work_expires_at", when_used="json")
    def serialize_work_deadline(self, value: datetime) -> str:
        return (
            value.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )


class SealedManagedDispatchPayload(GatewayProtocolModel):
    protocol_version: Literal[1] = 1
    key_id: str = Field(min_length=1, max_length=256)
    suite: Literal["dhkem_x25519_hkdf_sha256_hkdf_sha256_aes256_gcm"] = (
        "dhkem_x25519_hkdf_sha256_hkdf_sha256_aes256_gcm"
    )
    encapsulated_key_base64url: str = Field(min_length=1)
    ciphertext_base64url: str = Field(min_length=1)


class DispatchDeviceWorkPayload(GatewayProtocolModel):
    managed_work_id: str = Field(min_length=1, max_length=256)
    authenticated_data: ManagedDispatchAuthenticatedDataPayload
    sealed_envelope: SealedManagedDispatchPayload


class ManagedDispatchClaimsPayload(GatewayProtocolModel):
    iss: str = Field(min_length=1, max_length=256)
    aud: str = Field(min_length=1, max_length=256)
    authenticated_data: ManagedDispatchAuthenticatedDataPayload
    work: ManagedDeviceWorkPayload


class GatewayCommandTargetPayload(GatewayProtocolModel):
    device_id: str = Field(min_length=1, max_length=256)


class GatewayOpenCashDrawerCommandPayload(GatewayProtocolModel):
    kind: Literal["open_cash_drawer"] = "open_cash_drawer"


class GatewayPrintTestPageCommandPayload(GatewayProtocolModel):
    kind: Literal["print_test_page"] = "print_test_page"


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
    target: GatewayCommandTargetPayload
    command: GatewayDeviceCommandPayload
    metadata: JsonObject = Field(default_factory=dict)


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


class ControllerDispatchDeviceWorkMessage(GatewayProtocolModel):
    type: Literal[GatewayMessageType.CONTROLLER_DISPATCH_DEVICE_WORK] = (
        GatewayMessageType.CONTROLLER_DISPATCH_DEVICE_WORK
    )
    message_id: str
    command_id: str
    sequence: int = Field(ge=1)
    issued_at: datetime
    payload: DispatchDeviceWorkPayload


ControllerCommandMessage = Annotated[
    ControllerExecuteDeviceCommandMessage
    | ControllerCancelJobMessage
    | ControllerDispatchDeviceWorkMessage,
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
    job: JsonObject | None = None
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
