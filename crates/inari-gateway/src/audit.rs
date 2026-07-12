use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::identity::ActorId;
use crate::onboarding::InvitationId;
use crate::protocol::{AgentId, JobId, OrganizationId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    JobCreated,
    JobCancellationRequested,
    InvitationCreated,
    InvitationRevoked,
    AgentEnrolled,
    ZenohRead,
    ZenohWrite,
}

impl AuditAction {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::JobCreated => "job.created",
            Self::JobCancellationRequested => "job.cancellation_requested",
            Self::InvitationCreated => "invitation.created",
            Self::InvitationRevoked => "invitation.revoked",
            Self::AgentEnrolled => "agent.enrolled",
            Self::ZenohRead => "zenoh.read",
            Self::ZenohWrite => "zenoh.write",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum AuditResource {
    Controller,
    Agent { agent_id: AgentId },
    Invitation { invitation_id: InvitationId },
    Job { job_id: JobId },
    ZenohSelector { selector: String },
}

impl AuditResource {
    pub(crate) fn storage_parts(&self) -> (&'static str, Option<&str>) {
        match self {
            Self::Controller => ("controller", None),
            Self::Agent { agent_id } => ("agent", Some(agent_id.as_str())),
            Self::Invitation { invitation_id } => ("invitation", Some(invitation_id.as_str())),
            Self::Job { job_id } => ("job", Some(job_id.as_str())),
            Self::ZenohSelector { selector } => ("zenoh_selector", Some(selector)),
        }
    }

    pub(crate) fn from_storage(kind: &str, id: Option<String>) -> crate::GatewayResult<Self> {
        let required_id = || {
            id.clone().ok_or_else(|| {
                crate::GatewayError::CorruptState(format!(
                    "audit resource {kind:?} is missing its identifier"
                ))
            })
        };
        match kind {
            "controller" => Ok(Self::Controller),
            "agent" => Ok(Self::Agent { agent_id: required_id()?.parse()? }),
            "invitation" => Ok(Self::Invitation { invitation_id: required_id()?.parse()? }),
            "job" => Ok(Self::Job { job_id: required_id()?.parse()? }),
            "zenoh_selector" => Ok(Self::ZenohSelector { selector: required_id()? }),
            other => Err(crate::GatewayError::CorruptState(format!(
                "unknown audit resource kind {other:?}"
            ))),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditOutcome {
    Succeeded,
    Denied,
    Failed,
}

impl AuditOutcome {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Succeeded => "succeeded",
            Self::Denied => "denied",
            Self::Failed => "failed",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEvent {
    pub event_id: i64,
    pub actor_id: ActorId,
    pub action: AuditAction,
    pub resource: AuditResource,
    pub outcome: AuditOutcome,
    pub request_id: Option<String>,
    pub occurred_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct AuditEventDraft {
    pub organization_id: OrganizationId,
    pub actor_id: ActorId,
    pub action: AuditAction,
    pub resource: AuditResource,
    pub outcome: AuditOutcome,
    pub request_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AuditContext {
    pub actor_id: ActorId,
    pub request_id: Option<String>,
}

impl AuditContext {
    #[must_use]
    pub fn new(actor_id: ActorId, request_id: Option<String>) -> Self {
        Self { actor_id, request_id }
    }
}
