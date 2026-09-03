mod audit;
mod commands;
mod enrollment;
mod entity;
mod fleet;
mod managed_work;
mod onboarding;
mod publications;

use chrono::{DateTime, FixedOffset, Utc};
use jsonwebtoken::jwk::Jwk;
use sea_orm::{ConnectionTrait, DatabaseConnection, EntityTrait};

use crate::protocol::{
    AgentId, AgentPublication, ControllerCommand, DispatchEncryptionKey, GatewaySnapshot, JobId,
    JobState, OrganizationId, ProtocolVersion, SiteId,
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
    pub certificate_pem: Option<String>,
    pub namespace: String,
    pub protocol_version: ProtocolVersion,
    pub controller_actions: Vec<String>,
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
    pub command: ControllerCommand,
    pub issued_at: DateTime<Utc>,
    pub published_at: Option<DateTime<Utc>>,
    pub updated_at: DateTime<Utc>,
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
    pub error_code: Option<String>,
    pub message_key: String,
    pub admitted_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
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
