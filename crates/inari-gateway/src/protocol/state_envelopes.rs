use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{AgentId, DeviceId, ManagedWorkId, OrganizationId, PrintIntentId, ReportRoute, SiteId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrintJobState {
    Accepted,
    InProgress,
    OutputConfirmed,
    Failed,
    OutcomeUnknown,
    Expired,
    Canceled,
}

impl PrintJobState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Accepted => "accepted",
            Self::InProgress => "in_progress",
            Self::OutputConfirmed => "output_confirmed",
            Self::Failed => "failed",
            Self::OutcomeUnknown => "outcome_unknown",
            Self::Expired => "expired",
            Self::Canceled => "canceled",
        }
    }

    #[must_use]
    pub const fn is_terminal(self) -> bool {
        !matches!(self, Self::Accepted | Self::InProgress)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PrintJobOutputEvidence {
    Device,
    Spooler,
    Transport,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagedPrintOriginKind {
    Report,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedPrintJobOrigin {
    pub kind: ManagedPrintOriginKind,
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub database: String,
    pub company_id: String,
    pub report_binding_id: String,
    pub report_route: ReportRoute,
    pub report_action: String,
    pub source_model: String,
    pub record_ids: Vec<String>,
    pub wizard_input_digest: Option<String>,
    pub rendered_document_index: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedPrintJobSnapshot {
    pub print_job_id: String,
    pub print_intent_id: PrintIntentId,
    pub device_id: DeviceId,
    pub origin: ManagedPrintJobOrigin,
    pub managed_work_id: ManagedWorkId,
    pub state: PrintJobState,
    pub state_version: u64,
    pub accepted_at: DateTime<Utc>,
    pub started_at: Option<DateTime<Utc>>,
    pub terminal_at: Option<DateTime<Utc>>,
    pub expires_at: DateTime<Utc>,
    pub error_code: Option<String>,
    pub message_key: Option<String>,
    pub confirmation_evidence: Option<PrintJobOutputEvidence>,
    pub contract_version: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentStateObservation {
    pub contract_major: u16,
    pub envelope_id: String,
    pub agent_id: AgentId,
    pub agent_boot_id: String,
    pub reconciliation_session_id: String,
    pub dispatch_epoch: u64,
    pub envelope_sequence: u64,
    pub durable_state_sequence: u64,
    pub observed_at: DateTime<Utc>,
    pub issued_at: DateTime<Utc>,
    pub payload_fingerprint: String,
    pub job: ManagedPrintJobSnapshot,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SignedAgentStateObservation {
    pub observation: AgentStateObservation,
    pub state_envelope: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentStateReceipt {
    pub contract_major: u16,
    pub message_id: String,
    pub state_envelope_sha256: String,
}
