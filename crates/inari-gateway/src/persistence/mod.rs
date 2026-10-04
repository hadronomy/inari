mod audit;
mod commands;
mod enrollment;
mod entity;
mod fleet;
mod managed_work;
mod onboarding;
mod publications;
mod retirement;
mod router_policy;
mod state_observations;

pub use self::enrollment::InvitationAttemptLimit;
pub use self::router_policy::{
    NewRouterPolicy, PersistedRouterPolicy, RouterAdmission, RouterAgent,
};

use chrono::{DateTime, FixedOffset, Utc};
use jsonwebtoken::jwk::Jwk;
use sea_orm::{ConnectionTrait, DatabaseConnection, EntityTrait};
use serde::{Deserialize, Serialize};

use crate::protocol::{
    AgentId, AgentPublication, ControllerCommand, DispatchEncryptionKey, GatewaySnapshot, JobId,
    JobState, ManagedWorkId, ManagedWorkSubmission, OrganizationId, ProtocolVersion, SiteId,
};
use crate::{GatewayError, GatewayResult};

#[derive(Clone, Debug)]
pub struct GatewayRepository {
    pub(super) database: DatabaseConnection,
}

impl GatewayRepository {
    #[must_use]
    pub fn new(database: DatabaseConnection) -> Self {
        Self { database }
    }
}

#[derive(Debug, Clone)]
pub struct AgentEnrollmentRecord {
    pub agent_id: AgentId,
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub key_id: String,
    pub jwk_thumbprint: String,
    pub public_jwk: Jwk,
    pub dispatch_key: DispatchEncryptionKey,
    pub state_signing_jwk: Jwk,
    pub namespace: String,
    pub protocol_version: ProtocolVersion,
    pub controller_actions: Vec<String>,
    pub csr_fingerprint: String,
}

/// The result of an enrollment that committed or replayed.
///
/// `enrolled_at` is the time of the first enrollment, also for a replay.
#[derive(Debug, Clone)]
pub struct PreparedEnrollment<T> {
    pub value: T,
    pub enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedCommand {
    pub agent_id: AgentId,
    pub namespace: String,
    pub command_id: JobId,
    pub message_id: String,
    pub sequence: u64,
    pub state: JobState,
    pub command: CommandContent,
    pub issued_at: DateTime<Utc>,
    pub published_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(untagged, deny_unknown_fields)]
pub enum CommandContent {
    Inline(Box<ControllerCommand>),
    ManagedWork { managed_work_id: ManagedWorkId },
}

#[derive(Debug, Clone)]
pub struct PersistedPublication {
    pub key: String,
    pub received_at: DateTime<Utc>,
    pub message: AgentPublication,
}

#[derive(Debug, Clone)]
pub struct PersistedAgentStatus {
    pub message_id: String,
    pub received_at: DateTime<Utc>,
    pub snapshot: GatewaySnapshot,
}

#[derive(Debug, Clone)]
pub struct ManagedWorkTargetRecord {
    pub organization_id: OrganizationId,
    pub site_id: SiteId,
    pub agent_id: AgentId,
    pub device_id: crate::protocol::DeviceId,
    pub device_state: crate::protocol::DeviceState,
    pub capabilities: Vec<crate::protocol::DeviceCapability>,
    pub dispatch_key: DispatchEncryptionKey,
}

#[derive(Debug, Clone)]
pub struct NewManagedWorkPreflight {
    pub preflight_id: crate::protocol::ManagedPreflightId,
    pub request: crate::protocol::ManagedWorkPreflightRequest,
    pub dispatch_key_id: String,
    pub capability_digest: String,
    pub work_expires_at: DateTime<Utc>,
    pub idempotency_expires_at: DateTime<Utc>,
    pub submit_before: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedManagedWork {
    pub managed_work_id: crate::protocol::ManagedWorkId,
    pub print_intent_id: crate::protocol::PrintIntentId,
    pub scope: crate::protocol::ManagedWorkScope,
    pub device_id: crate::protocol::DeviceId,
    pub operation: crate::protocol::ManagedDocumentOperation,
    pub state: crate::protocol::ManagedWorkState,
    pub print_job_id: Option<String>,
    pub print_job_observation: Option<crate::protocol::SignedAgentStateObservation>,
    pub error_code: Option<String>,
    pub message_key: String,
    pub admitted_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedManagedWorkDispatch {
    pub managed_work: PersistedManagedWork,
    pub command: Option<PersistedCommand>,
}

#[derive(Debug, Clone)]
pub struct NewManagedPayload {
    pub ciphertext: Vec<u8>,
    pub nonce: [u8; 12],
    pub wrapped_data_key: String,
    pub wrapping_key_version: u32,
    pub authenticated_data_digest: [u8; 32],
    pub plaintext_bytes: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct ManagedPayloadContext {
    pub organization_id: String,
    pub managed_work_id: ManagedWorkId,
    pub idempotency_key: String,
    pub payload_fingerprint: String,
    pub request_fingerprint: String,
}

#[derive(Debug, Clone)]
pub struct PersistedManagedPayload {
    pub protection: NewManagedPayload,
    pub context: ManagedPayloadContext,
}

pub struct ManagedDispatchAllocation<'a> {
    pub managed_work_id: &'a ManagedWorkId,
    pub sequence: u64,
    pub command_id: &'a str,
    pub message_id: &'a str,
    pub issued_at: DateTime<Utc>,
    pub recipient_key: &'a crate::protocol::DispatchEncryptionKey,
    pub work_expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy)]
pub struct ManagedWorkAdmission<'a> {
    pub router_admission: &'a RouterAdmission,
    pub managed_work_id: &'a ManagedWorkId,
    pub idempotency_key: &'a str,
    pub submission: &'a ManagedWorkSubmission,
    pub request_fingerprint: &'a [u8; 32],
    pub payload_fingerprint: &'a [u8; 32],
    pub payload_bytes: i64,
    pub admitted_at: DateTime<Utc>,
}

fn stored_time(value: DateTime<Utc>) -> DateTime<FixedOffset> {
    value.fixed_offset()
}

fn utc_time(value: DateTime<FixedOffset>) -> DateTime<Utc> {
    value.with_timezone(&Utc)
}

async fn require_agent<C>(database: &C, agent_id: &str) -> GatewayResult<entity::agent::Model>
where
    C: ConnectionTrait,
{
    entity::agent::Entity::find_by_id(agent_id)
        .one(database)
        .await?
        .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))
}
