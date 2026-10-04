use chrono::{DateTime, Duration, Utc};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ConnectionTrait, DbBackend, EntityTrait, FromQueryResult,
    QuerySelect, Statement, TransactionTrait,
};
use serde_json::Value;

use super::entity::router_policy;
use super::{GatewayRepository, stored_time, utc_time};
use crate::{GatewayError, GatewayResult};

const ACKNOWLEDGMENT_LIFETIME: Duration = Duration::seconds(10);

#[derive(Debug, Clone, FromQueryResult)]
pub struct RouterAgent {
    pub agent_id: String,
    pub namespace: String,
}

#[derive(Debug, Clone)]
pub struct NewRouterPolicy {
    pub signed_policy: Value,
    pub digest: [u8; 32],
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedRouterPolicy {
    pub generation: u64,
    pub signed_policy: Value,
    pub digest: [u8; 32],
    pub expires_at: DateTime<Utc>,
    pub agents: Vec<RouterAgent>,
}

/// Proof that every configured Router acknowledged the current durable policy.
/// The admission transaction rechecks this proof while it holds a shared policy lock.
#[derive(Debug, Clone)]
pub struct RouterAdmission {
    organization_id: String,
    generation: i64,
    digest: [u8; 32],
    valid_until: DateTime<Utc>,
}

impl RouterAdmission {
    #[must_use]
    pub fn valid_until(&self) -> DateTime<Utc> {
        self.valid_until
    }

    pub(super) fn organization_id(&self) -> &str {
        &self.organization_id
    }
}

impl GatewayRepository {
    /// Allocates one generation across Controller replicas. `build` must not use the network.
    /// Agent and key changes invalidate the acknowledgment in the same transaction as the change.
    pub async fn prepare_router_policy<F>(
        &self,
        organization_id: &str,
        configuration_digest: &[u8; 32],
        refresh_before: DateTime<Utc>,
        build: F,
    ) -> GatewayResult<PersistedRouterPolicy>
    where
        F: FnOnce(u64, &[RouterAgent]) -> GatewayResult<NewRouterPolicy>,
    {
        let transaction = self.database.begin().await?;
        let row = policy_in(&transaction, organization_id, false).await?;
        let agents = active_agents(&transaction, organization_id).await?;
        let reuse = row.policy_revision == Some(row.authority_revision)
            && row.configuration_digest.as_deref() == Some(configuration_digest.as_slice())
            && row
                .expires_at
                .is_some_and(|time| utc_time(time) > refresh_before);
        let row = if reuse {
            row
        } else {
            let generation = row
                .generation
                .checked_add(1)
                .filter(|generation| *generation <= 9_007_199_254_740_991)
                .ok_or_else(|| {
                    GatewayError::Capacity("Router policy generation is exhausted".into())
                })?;
            let prepared = build(u64::try_from(generation).map_err(|_| corrupt())?, &agents)?;
            if prepared.expires_at <= Utc::now() {
                return Err(GatewayError::InvalidInput("Router policy is already expired".into()));
            }
            let revision = row.authority_revision;
            let mut update: router_policy::ActiveModel = row.into();
            update.policy_revision = Set(Some(revision));
            update.generation = Set(generation);
            update.configuration_digest = Set(Some(configuration_digest.to_vec()));
            update.policy_digest = Set(Some(prepared.digest.to_vec()));
            update.signed_policy = Set(Some(prepared.signed_policy));
            update.expires_at = Set(Some(stored_time(prepared.expires_at)));
            update.acknowledged_at = Set(None);
            update.update(&transaction).await?
        };
        let policy = persisted(row, agents)?;
        transaction.commit().await?;
        Ok(policy)
    }

    /// Records a complete Router acknowledgment only while its authority remains current.
    pub async fn acknowledge_router_policy(
        &self,
        organization_id: &str,
        policy: &PersistedRouterPolicy,
    ) -> GatewayResult<RouterAdmission> {
        let transaction = self.database.begin().await?;
        let row = policy_in(&transaction, organization_id, false).await?;
        let generation = i64::try_from(policy.generation).map_err(|_| corrupt())?;
        if row.generation != generation
            || row.policy_digest.as_deref() != Some(policy.digest.as_slice())
            || row.policy_revision != Some(row.authority_revision)
            || row
                .expires_at
                .is_none_or(|time| utc_time(time) <= Utc::now())
        {
            return Err(GatewayError::Conflict(
                "Router authority changed during acknowledgment".into(),
            ));
        }
        let mut update: router_policy::ActiveModel = row.into();
        let now = Utc::now();
        update.acknowledged_at = Set(Some(stored_time(now)));
        update.update(&transaction).await?;
        transaction.commit().await?;
        Ok(RouterAdmission {
            organization_id: organization_id.into(),
            generation,
            digest: policy.digest,
            valid_until: policy
                .expires_at
                .min(now + ACKNOWLEDGMENT_LIFETIME),
        })
    }

    pub async fn require_router_admission(
        &self,
        admission: &RouterAdmission,
        agent_id: &str,
    ) -> GatewayResult<()> {
        let transaction = self.database.begin().await?;
        require_admission_in(&transaction, admission, agent_id).await?;
        transaction.commit().await?;
        Ok(())
    }
}

pub(super) async fn require_admission_in<C: ConnectionTrait>(
    database: &C,
    admission: &RouterAdmission,
    agent_id: &str,
) -> GatewayResult<()> {
    let row = policy_in(database, &admission.organization_id, true).await?;
    let now = Utc::now();
    if admission.valid_until <= now
        || row.generation != admission.generation
        || row.policy_digest.as_deref() != Some(admission.digest.as_slice())
        || row.policy_revision != Some(row.authority_revision)
        || row
            .expires_at
            .is_none_or(|time| utc_time(time) <= now)
        || row
            .acknowledged_at
            .is_none_or(|time| utc_time(time) <= now - ACKNOWLEDGMENT_LIFETIME)
        || !is_active_agent(database, &admission.organization_id, agent_id).await?
    {
        return Err(GatewayError::Unavailable("The Agent has no current Router admission".into()));
    }
    Ok(())
}

async fn policy_in<C: ConnectionTrait>(
    database: &C,
    organization_id: &str,
    shared: bool,
) -> GatewayResult<router_policy::Model> {
    let query = router_policy::Entity::find_by_id(organization_id);
    let query = if shared { query.lock_shared() } else { query.lock_exclusive() };
    query
        .one(database)
        .await?
        .ok_or_else(|| GatewayError::Unavailable("Router policy persistence is unavailable".into()))
}

async fn active_agents<C: ConnectionTrait>(
    database: &C,
    organization_id: &str,
) -> GatewayResult<Vec<RouterAgent>> {
    Ok(RouterAgent::find_by_statement(Statement::from_sql_and_values(
        DbBackend::Postgres,
        "SELECT a.agent_id, a.namespace FROM agents a WHERE a.organization_id = $1
         AND a.dispatch_key IS NOT NULL
         AND EXISTS (SELECT 1 FROM agent_verification_keys k WHERE k.agent_id = a.agent_id
             AND k.purpose = 'transport_identity' AND k.key_id = a.key_id AND k.retired_at IS NULL)
         AND EXISTS (SELECT 1 FROM agent_verification_keys k WHERE k.agent_id = a.agent_id
             AND k.purpose = 'agent_state' AND k.retired_at IS NULL)
         ORDER BY a.agent_id",
        [organization_id.to_owned().into()],
    ))
    .all(database)
    .await?)
}

fn persisted(
    row: router_policy::Model,
    agents: Vec<RouterAgent>,
) -> GatewayResult<PersistedRouterPolicy> {
    Ok(PersistedRouterPolicy {
        generation: u64::try_from(row.generation).map_err(|_| corrupt())?,
        signed_policy: row.signed_policy.ok_or_else(corrupt)?,
        digest: row
            .policy_digest
            .ok_or_else(corrupt)?
            .try_into()
            .map_err(|_| corrupt())?,
        expires_at: utc_time(row.expires_at.ok_or_else(corrupt)?),
        agents,
    })
}

fn corrupt() -> GatewayError {
    GatewayError::CorruptState("Router policy is incomplete".into())
}

#[derive(FromQueryResult)]
struct ActiveAgent {
    present: bool,
}

async fn is_active_agent<C: ConnectionTrait>(
    database: &C,
    organization_id: &str,
    agent_id: &str,
) -> GatewayResult<bool> {
    let active = ActiveAgent::find_by_statement(Statement::from_sql_and_values(
        DbBackend::Postgres,
        "SELECT EXISTS (SELECT 1 FROM agents a WHERE a.organization_id = $1 AND a.agent_id = $2
         AND a.dispatch_key IS NOT NULL
         AND EXISTS (SELECT 1 FROM agent_verification_keys k WHERE k.agent_id = a.agent_id
             AND k.purpose = 'transport_identity' AND k.key_id = a.key_id AND k.retired_at IS NULL)
         AND EXISTS (SELECT 1 FROM agent_verification_keys k WHERE k.agent_id = a.agent_id
             AND k.purpose = 'agent_state' AND k.retired_at IS NULL)) AS present",
        [organization_id.to_owned().into(), agent_id.to_owned().into()],
    ))
    .one(database)
    .await?
    .ok_or_else(corrupt)?;
    Ok(active.present)
}
