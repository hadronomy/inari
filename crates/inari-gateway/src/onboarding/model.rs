use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use url::Url;

use super::InvitationId;
use crate::protocol::{GatewaySnapshot, SiteId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvitationState {
    Created,
    Claimed,
    Enrolled,
    Online,
    Expired,
    Failed,
    Revoked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CertificateMode {
    None,
    Controller,
    StepCa,
}

impl InvitationState {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Created => "created",
            Self::Claimed => "claimed",
            Self::Enrolled => "enrolled",
            Self::Online => "online",
            Self::Expired => "expired",
            Self::Failed => "failed",
            Self::Revoked => "revoked",
        }
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct CreateInvitation {
    pub label: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IssuedInvitation {
    pub invitation_id: InvitationId,
    pub invitation_url: Url,
    pub manual_code: String,
    pub expires_at: DateTime<Utc>,
    pub state: InvitationState,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvitationStatus {
    pub invitation_id: InvitationId,
    pub site_id: SiteId,
    pub label: Option<String>,
    pub state: InvitationState,
    pub created_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub claimed_at: Option<DateTime<Utc>>,
    pub enrolled_at: Option<DateTime<Utc>>,
    pub online_at: Option<DateTime<Utc>>,
    pub revoked_at: Option<DateTime<Utc>>,
    pub failed_at: Option<DateTime<Utc>>,
    pub last_error: Option<String>,
    pub agent_id: Option<String>,
    pub key_id: Option<String>,
    pub latest_snapshot: Option<GatewaySnapshot>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvitationPreview {
    pub invitation_id: InvitationId,
    pub site_id: SiteId,
    pub expires_at: DateTime<Utc>,
    pub state: InvitationState,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub supported_protocol_versions: Vec<crate::protocol::ProtocolVersion>,
    pub certificate_mode: CertificateMode,
    pub requires_mutual_tls_after_issuance: bool,
}
