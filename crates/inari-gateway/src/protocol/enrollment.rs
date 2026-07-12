use chrono::{DateTime, Utc};
use jsonwebtoken::jwk::Jwk;
use serde::{Deserialize, Serialize};

use super::{AgentId, GatewaySnapshot, ProtocolDescriptor, ProtocolVersion};

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
