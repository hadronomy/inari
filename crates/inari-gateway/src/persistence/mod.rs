mod audit;
mod commands;
mod enrollment;
mod entity;
mod fleet;
mod onboarding;
mod publications;

use chrono::{DateTime, FixedOffset, Utc};
use jsonwebtoken::jwk::Jwk;
use sea_orm::{ConnectionTrait, DatabaseConnection, EntityTrait};

use crate::protocol::{
    AgentId, AgentPublication, ControllerCommand, GatewaySnapshot, JobId, JobState, OrganizationId,
    ProtocolVersion, SiteId,
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
