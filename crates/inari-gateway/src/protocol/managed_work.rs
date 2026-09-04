use chrono::{DateTime, Utc};
use jsonwebtoken::jwk::Jwk;
use serde::{Deserialize, Serialize};

use super::{
    AgentId, DeviceId, ManagedPreflightId, ManagedWorkId, OrganizationId, PrintIntentId, SiteId,
    StructuredFields,
};

pub const MANAGED_WORK_CONTRACT_MAJOR: u16 = 1;
pub const MANAGED_DISPATCH_VERSION: u16 = 1;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkScope {
    pub database: String,
    pub company_id: String,
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub agent_id: AgentId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AgentManagedScope {
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub agent_id: AgentId,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedDispatchEnrollment {
    pub scope: AgentManagedScope,
    pub issuer: String,
    pub epoch: u64,
    pub verification_jwk: Jwk,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagedDocumentOperation {
    ReportPdf,
    LabelDocument,
}

impl ManagedDocumentOperation {
    #[must_use]
    pub const fn media_type(self) -> &'static str {
        match self {
            Self::ReportPdf => "application/pdf",
            Self::LabelDocument => "application/vnd.zebra-zpl",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReportBindingClaim {
    pub report_binding_id: String,
    pub binding_revision_id: String,
    pub report_action_id: String,
    pub report_contract_digest: String,
    pub template_digest: String,
    pub command_profile_id: Option<String>,
    pub layout_profile_id: Option<String>,
    pub hardware_matrix_digest: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkPreflightRequest {
    pub contract_major: u16,
    pub scope: ManagedWorkScope,
    pub device_id: DeviceId,
    pub operation: ManagedDocumentOperation,
    pub binding: ReportBindingClaim,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagedWorkPreflightState {
    Ready,
    Blocked,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkPreflightResult {
    pub contract_major: u16,
    pub preflight_id: Option<ManagedPreflightId>,
    pub state: ManagedWorkPreflightState,
    pub operation: ManagedDocumentOperation,
    pub media_type: String,
    pub device_id: DeviceId,
    pub capability_digest: Option<String>,
    pub expires_at: Option<DateTime<Utc>>,
    pub idempotency_expires_at: Option<DateTime<Utc>>,
    pub submit_before: Option<DateTime<Utc>>,
    pub error_code: Option<String>,
    pub message_key: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ReportSource {
    Records { model: String, ordered_ids: Vec<i64> },
    Wizard { model: String, input_digest: String },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReportRoute {
    Manual,
    Automatic,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReportPrintOrigin {
    pub binding: ReportBindingClaim,
    pub route: ReportRoute,
    pub source: ReportSource,
    pub rendered_document_index: u32,
    pub copy_ordinal: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
pub enum ManagedDocument {
    ReportPdf { content_base64: String },
    LabelDocument { content_base64: String },
}

impl ManagedDocument {
    #[must_use]
    pub const fn operation(&self) -> ManagedDocumentOperation {
        match self {
            Self::ReportPdf { .. } => ManagedDocumentOperation::ReportPdf,
            Self::LabelDocument { .. } => ManagedDocumentOperation::LabelDocument,
        }
    }

    #[must_use]
    pub const fn media_type(&self) -> &'static str {
        self.operation().media_type()
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedDeviceWork {
    pub contract_major: u16,
    pub scope: ManagedWorkScope,
    pub print_intent_id: PrintIntentId,
    pub device_id: DeviceId,
    pub origin: ReportPrintOrigin,
    pub document: ManagedDocument,
    #[serde(default)]
    pub normalized_device_options: StructuredFields,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchKem {
    DhkemX25519HkdfSha256,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DispatchHpkeSuite {
    DhkemX25519HkdfSha256HkdfSha256Aes256Gcm,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DispatchEncryptionKey {
    pub key_id: String,
    pub kem: DispatchKem,
    pub public_key_base64url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SealedManagedDispatch {
    pub protocol_version: u16,
    pub key_id: String,
    pub suite: DispatchHpkeSuite,
    pub encapsulated_key_base64url: String,
    pub ciphertext_base64url: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkSubmission {
    pub contract_major: u16,
    pub preflight_id: ManagedPreflightId,
    pub payload_fingerprint: String,
    pub work: ManagedDeviceWork,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagedWorkState {
    PendingAgent,
    Dispatching,
    Accepted,
    Rejected,
    Canceled,
    Expired,
    RecoveryUncertain,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkReceipt {
    pub managed_work_id: ManagedWorkId,
    pub state: ManagedWorkState,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedWorkRecord {
    pub managed_work_id: ManagedWorkId,
    pub print_intent_id: PrintIntentId,
    pub scope: ManagedWorkScope,
    pub device_id: DeviceId,
    pub operation: ManagedDocumentOperation,
    pub media_type: String,
    pub state: ManagedWorkState,
    pub print_job_id: Option<String>,
    pub error_code: Option<String>,
    pub message_key: String,
    pub admitted_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DispatchDeviceWork {
    pub managed_work_id: ManagedWorkId,
    pub authenticated_data: ManagedDispatchAuthenticatedData,
    pub sealed_envelope: SealedManagedDispatch,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedDispatchAuthenticatedData {
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub agent_id: AgentId,
    pub managed_work_id: ManagedWorkId,
    pub idempotency_key: String,
    pub payload_fingerprint: String,
    pub dispatch_epoch: u64,
    pub sequence: u64,
    pub issued_at: i64,
    pub expires_at: i64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedDispatchClaims {
    #[serde(rename = "iss")]
    pub issuer: String,
    #[serde(rename = "aud")]
    pub audience: AgentId,
    pub authenticated_data: ManagedDispatchAuthenticatedData,
    pub work: ManagedDeviceWork,
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::{
        MANAGED_WORK_CONTRACT_MAJOR, ManagedDeviceWork, ManagedDocument, ManagedDocumentOperation,
    };

    #[test]
    fn document_variants_fix_the_operation_and_media_type() {
        let pdf = ManagedDocument::ReportPdf { content_base64: "JVBERi0xLjQ=".into() };
        let label = ManagedDocument::LabelDocument { content_base64: "XlhB".into() };

        assert_eq!(pdf.operation(), ManagedDocumentOperation::ReportPdf);
        assert_eq!(pdf.media_type(), "application/pdf");
        assert_eq!(label.operation(), ManagedDocumentOperation::LabelDocument);
        assert_eq!(label.media_type(), "application/vnd.zebra-zpl");
        assert_eq!(MANAGED_WORK_CONTRACT_MAJOR, 1);
    }

    #[test]
    fn submission_rejects_browser_selected_transport_and_media_type() {
        let result = serde_json::from_value::<ManagedDeviceWork>(json!({
            "contract_major": 1,
            "scope": {
                "database": "mze-production",
                "company_id": "7",
                "organization_id": "org_mze",
                "site_id": "site_tenerife",
                "agent_id": "agt_tenerife"
            },
            "print_intent_id": "pi_v1_abc123",
            "device_id": "dev_report_printer",
            "origin": {
                "binding": {
                    "report_binding_id": "report-binding-1",
                    "binding_revision_id": "revision-1",
                    "report_action_id": "sale.action_report_saleorder",
                    "report_contract_digest": "contract-digest",
                    "template_digest": "template-digest",
                    "command_profile_id": null,
                    "layout_profile_id": null,
                    "hardware_matrix_digest": null
                },
                "route": "manual",
                "source": {"kind": "records", "model": "sale.order", "ordered_ids": [42]},
                "rendered_document_index": 0,
                "copy_ordinal": 1
            },
            "document": {
                "operation": "report_pdf",
                "content_base64": "JVBERi0xLjQ=",
                "media_type": "application/postscript",
                "transport": "raw"
            },
            "normalized_device_options": {},
            "expires_at": "2026-09-03T12:05:00Z",
            "idempotency_expires_at": "2026-12-02T12:00:00Z"
        }));

        assert!(result.is_err());
    }
}
