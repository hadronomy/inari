use std::collections::BTreeMap;
use std::fmt;
use std::str::FromStr;

use chrono::{DateTime, NaiveDate, Utc};
use jsonwebtoken::jwk::Jwk;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const GATEWAY_PROTOCOL_VERSION: &str = "2026-07-11";
const AGENT_ID_PREFIX: &str = "agt_";
const MAX_AGENT_ID_LENGTH: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct AgentId(String);

impl AgentId {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AgentId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for AgentId {
    type Err = crate::GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let suffix = value
            .strip_prefix(AGENT_ID_PREFIX)
            .filter(|suffix| !suffix.is_empty());
        let valid = value.len() <= MAX_AGENT_ID_LENGTH
            && suffix.is_some_and(|suffix| {
                suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
            });
        if !valid {
            return Err(crate::GatewayError::InvalidInput(
                "agent IDs must start with `agt_`, contain only lowercase ASCII letters, digits, or underscores, and be at most 64 characters"
                    .into(),
            ));
        }
        Ok(Self(value.into()))
    }
}

impl TryFrom<String> for AgentId {
    type Error = crate::GatewayError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl From<AgentId> for String {
    fn from(agent_id: AgentId) -> Self {
        agent_id.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct ProtocolVersion(String);

impl ProtocolVersion {
    #[must_use]
    pub fn current() -> Self {
        Self(GATEWAY_PROTOCOL_VERSION.into())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ProtocolVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ProtocolVersion {
    type Err = crate::GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let valid_shape = value.len() == 10
            && value
                .bytes()
                .enumerate()
                .all(|(index, byte)| {
                    matches!(index, 4 | 7) && byte == b'-'
                        || !matches!(index, 4 | 7) && byte.is_ascii_digit()
                });
        if !valid_shape || NaiveDate::parse_from_str(value, "%Y-%m-%d").is_err() {
            return Err(crate::GatewayError::InvalidInput(
                "protocol versions must be valid ISO 8601 calendar dates".into(),
            ));
        }
        Ok(Self(value.into()))
    }
}

impl TryFrom<String> for ProtocolVersion {
    type Error = crate::GatewayError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl From<ProtocolVersion> for String {
    fn from(version: ProtocolVersion) -> Self {
        version.0
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProtocolDescriptor {
    pub version: ProtocolVersion,
    pub supported_versions: Vec<ProtocolVersion>,
}

impl Default for ProtocolDescriptor {
    fn default() -> Self {
        Self {
            version: ProtocolVersion::current(),
            supported_versions: vec![ProtocolVersion::current()],
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewaySnapshot {
    pub generated_at: DateTime<Utc>,
    pub protocol: ProtocolDescriptor,
    pub service: ServiceDescriptor,
    pub security: SecurityDescriptor,
    pub runtime: RuntimeDescriptor,
    pub capabilities: CapabilityDescriptor,
    #[serde(default)]
    pub observability: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentStatus {
    pub agent_id: AgentId,
    pub message_id: String,
    pub received_at: DateTime<Utc>,
    pub snapshot: GatewaySnapshot,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ServiceDescriptor {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default, flatten)]
    pub attributes: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecurityDescriptor {
    pub mode: String,
    pub exposure: String,
    pub tls_required: bool,
    pub certificate_mode: String,
    pub mutual_tls_mode: String,
    pub mutual_tls_enabled: bool,
    #[serde(default)]
    pub certificate_expires_at: Option<DateTime<Utc>>,
    #[serde(default, flatten)]
    pub attributes: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RuntimeDescriptor {
    #[serde(default)]
    pub queue: BTreeMap<String, u64>,
    #[serde(default)]
    pub devices: BTreeMap<String, Value>,
    #[serde(default)]
    pub inventory: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CapabilityDescriptor {
    #[serde(default)]
    pub supported_content_kinds: Vec<String>,
    #[serde(default)]
    pub supported_device_commands: Vec<String>,
    #[serde(default)]
    pub supported_controller_actions: Vec<String>,
    #[serde(default)]
    pub features: Vec<String>,
    pub transport: String,
    #[serde(default)]
    pub client_certificate_present: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnrollmentRequest {
    #[serde(default)]
    pub protocol: ProtocolDescriptor,
    pub agent_id: AgentId,
    pub key_id: String,
    pub public_jwk: Jwk,
    #[serde(default)]
    pub certificate_pem: Option<String>,
    pub csr_pem: String,
    pub snapshot: GatewaySnapshot,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnrollmentResponse {
    pub selected_protocol_version: ProtocolVersion,
    pub controller: ControllerInfo,
    pub permissions: EnrollmentPermissions,
    pub data_plane: DataPlane,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub certificate: Option<CertificateProvisioning>,
    pub enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ControllerInfo {
    pub name: Option<String>,
    pub instance_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EnrollmentPermissions {
    pub controller_actions: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataPlane {
    pub kind: DataPlaneKind,
    pub session_mode: SessionMode,
    pub connect_endpoints: Vec<String>,
    pub namespace: String,
    pub serialization: Serialization,
    pub auth: DataPlaneAuth,
    pub tls: DataPlaneTls,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DataPlaneKind {
    Zenoh,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionMode {
    Client,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Serialization {
    Json,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataPlaneAuth {
    pub kind: DataPlaneAuthKind,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DataPlaneAuthKind {
    Mtls,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DataPlaneTls {
    pub close_link_on_expiration: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case")]
pub enum CertificateProvisioning {
    StepCa { enrollment: StepCaEnrollment },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepCaEnrollment {
    pub base_url: String,
    pub trust: CertificateTrust,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub bootstrap_auth: Option<CertificateBootstrapAuth>,
    pub subject: Option<String>,
    pub authorized_sans: Vec<String>,
    pub requires_mutual_tls_after_issuance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CertificateTrust {
    pub root_fingerprint: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CertificateBootstrapAuth {
    #[serde(rename = "type")]
    pub kind: CertificateBootstrapAuthKind,
    pub token: Option<String>,
    pub expires_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CertificateBootstrapAuthKind {
    Ott,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ControllerCommand {
    #[serde(rename = "controller.command.submit_print_job")]
    SubmitPrintJob {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        payload: SubmitPrintJob,
    },
    #[serde(rename = "controller.command.execute_device_command")]
    ExecuteDeviceCommand {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        payload: ExecuteDeviceCommand,
    },
    #[serde(rename = "controller.command.cancel_job")]
    CancelJob {
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
        job_id: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmitPrintJob {
    pub content: PrintContent,
    #[serde(default)]
    pub target: CommandTarget,
    #[serde(default)]
    pub options: PrintOptions,
    #[serde(default)]
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PrintContent {
    StructuredReceipt { data: Value, document_name: String },
    ReceiptImage { binary: BinaryContent, document_name: String },
    Text { text: String, document_name: String },
    Html { html: String, document_name: String },
    Pdf { binary: BinaryContent, document_name: String },
    Raw { binary: BinaryContent, data_type: String, document_name: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BinaryContent {
    pub base64: String,
    pub declared_mime_type: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CommandTarget {
    pub device_id: Option<String>,
    pub printer_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrintOptions {
    pub transport: String,
    pub open_cash_drawer: bool,
}

impl Default for PrintOptions {
    fn default() -> Self {
        Self { transport: "auto".into(), open_cash_drawer: false }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecuteDeviceCommand {
    #[serde(default)]
    pub target: CommandTarget,
    pub command: DeviceCommand,
    #[serde(default)]
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum DeviceCommand {
    OpenCashDrawer,
    PrintTestPage { transport: String },
    FeedLines { count: u8 },
    FeedDots { count: u8 },
    CutPaper { mode: String },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmitControllerCommandRequest {
    pub agent_id: String,
    #[serde(default)]
    pub command_id: Option<String>,
    #[serde(flatten)]
    pub command: ControllerCommandRequest,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum ControllerCommandRequest {
    #[serde(rename = "controller.command.submit_print_job")]
    SubmitPrintJob { payload: SubmitPrintJob },
    #[serde(rename = "controller.command.execute_device_command")]
    ExecuteDeviceCommand { payload: ExecuteDeviceCommand },
    #[serde(rename = "controller.command.cancel_job")]
    CancelJob { job_id: String },
}

impl ControllerCommandRequest {
    #[must_use]
    pub const fn required_action(&self) -> &'static str {
        match self {
            Self::SubmitPrintJob { .. } => "jobs:create",
            Self::ExecuteDeviceCommand { .. } => "commands:execute",
            Self::CancelJob { .. } => "jobs:cancel",
        }
    }

    #[must_use]
    pub fn into_message(
        self,
        message_id: String,
        command_id: String,
        sequence: u64,
        issued_at: DateTime<Utc>,
    ) -> ControllerCommand {
        match self {
            Self::SubmitPrintJob { payload } => ControllerCommand::SubmitPrintJob {
                message_id,
                command_id,
                sequence,
                issued_at,
                payload,
            },
            Self::ExecuteDeviceCommand { payload } => ControllerCommand::ExecuteDeviceCommand {
                message_id,
                command_id,
                sequence,
                issued_at,
                payload,
            },
            Self::CancelJob { job_id } => {
                ControllerCommand::CancelJob { message_id, command_id, sequence, issued_at, job_id }
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControllerCommandState {
    Queued,
    Published,
    Accepted,
    Rejected,
    Completed,
    Failed,
    Superseded,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubmitControllerCommandResponse {
    pub command: ControllerCommand,
    pub state: ControllerCommandState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CommandHistoryResponse {
    pub selected_protocol_version: ProtocolVersion,
    pub commands: Vec<ControllerCommand>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentPublicationList {
    pub publications: Vec<StoredAgentPublication>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StoredAgentPublication {
    pub key: String,
    pub received_at: DateTime<Utc>,
    pub message: AgentPublication,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum AgentPublication {
    #[serde(rename = "agent.command.accepted")]
    CommandAccepted {
        message_id: String,
        command_id: String,
        accepted_at: DateTime<Utc>,
        #[serde(default)]
        job: Option<BTreeMap<String, Value>>,
        detail: String,
    },
    #[serde(rename = "agent.command.rejected")]
    CommandRejected {
        message_id: String,
        command_id: String,
        rejected_at: DateTime<Utc>,
        code: String,
        detail: String,
    },
    #[serde(rename = "agent.runtime.event")]
    RuntimeEvent {
        message_id: String,
        occurred_at: DateTime<Utc>,
        event: RuntimeEvent,
        #[serde(default)]
        command_id: Option<String>,
        #[serde(default)]
        job_id: Option<String>,
    },
    #[serde(rename = "agent.status.snapshot")]
    StatusSnapshot { message_id: String, snapshot: Box<GatewaySnapshot> },
    #[serde(rename = "agent.error")]
    Error {
        message_id: String,
        occurred_at: DateTime<Utc>,
        code: String,
        detail: String,
        #[serde(default)]
        command_id: Option<String>,
        #[serde(default)]
        retriable: bool,
    },
}

impl AgentPublication {
    #[must_use]
    pub fn message_id(&self) -> &str {
        match self {
            Self::CommandAccepted { message_id, .. }
            | Self::CommandRejected { message_id, .. }
            | Self::RuntimeEvent { message_id, .. }
            | Self::StatusSnapshot { message_id, .. }
            | Self::Error { message_id, .. } => message_id,
        }
    }

    #[must_use]
    pub const fn message_type(&self) -> &'static str {
        match self {
            Self::CommandAccepted { .. } => "agent.command.accepted",
            Self::CommandRejected { .. } => "agent.command.rejected",
            Self::RuntimeEvent { .. } => "agent.runtime.event",
            Self::StatusSnapshot { .. } => "agent.status.snapshot",
            Self::Error { .. } => "agent.error",
        }
    }

    #[must_use]
    pub fn command_id(&self) -> Option<&str> {
        match self {
            Self::CommandAccepted { command_id, .. } | Self::CommandRejected { command_id, .. } => {
                Some(command_id)
            },
            Self::RuntimeEvent { command_id, .. } | Self::Error { command_id, .. } => {
                command_id.as_deref()
            },
            Self::StatusSnapshot { .. } => None,
        }
    }

    #[must_use]
    pub fn snapshot(&self) -> Option<&GatewaySnapshot> {
        match self {
            Self::StatusSnapshot { snapshot, .. } => Some(snapshot.as_ref()),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeEvent {
    pub sequence: u64,
    pub resource_kind: String,
    pub resource_id: String,
    pub event_type: String,
    pub occurred_at: DateTime<Utc>,
    #[serde(default)]
    pub payload: BTreeMap<String, Value>,
}

#[cfg(test)]
mod tests {
    use super::{AgentId, ProtocolVersion};

    #[test]
    fn agent_id_accepts_a_single_safe_key_expression_segment() {
        let agent_id = "agt_browser_audit"
            .parse::<AgentId>()
            .expect("agent ID should parse");

        assert_eq!(agent_id.as_str(), "agt_browser_audit");
        assert_eq!(serde_json::to_string(&agent_id).unwrap(), "\"agt_browser_audit\"");
    }

    #[test]
    fn agent_id_rejects_path_syntax_and_unicode() {
        for value in ["browser_audit", "agt_", "agt_browser/audit", "agt_*", "agt_浏览器"] {
            assert!(value.parse::<AgentId>().is_err(), "accepted {value:?}");
        }
    }

    #[test]
    fn protocol_version_round_trips_as_an_iso_date() {
        let version = ProtocolVersion::current();
        let json = serde_json::to_string(&version).expect("protocol version should serialize");

        assert_eq!(json, "\"2026-07-11\"");
        assert_eq!(
            serde_json::from_str::<ProtocolVersion>(&json)
                .expect("protocol version should deserialize"),
            version,
        );
    }

    #[test]
    fn protocol_version_rejects_invalid_dates_and_shapes() {
        for value in ["2026-02-30", "2026-7-11", "latest", "２０２６-０７-１１"] {
            assert!(
                value
                    .parse::<ProtocolVersion>()
                    .is_err(),
                "accepted {value:?}"
            );
        }
    }
}
