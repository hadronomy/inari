use chrono::{DateTime, Utc};
use sea_orm::sea_query::OnConflict;
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, DatabaseTransaction, EntityTrait, QueryFilter,
    QuerySelect,
};

use super::entity::value::{
    AgentKeyPurpose, CommandState, ManagedWorkStateValue, StoredAgentStateObservation,
};
use super::entity::{
    agent_state_observation, agent_verification_key, command, managed_payload, managed_work,
};
use super::{CommandContent, stored_time, utc_time};
use crate::protocol::{
    AgentPublication, AgentStateObservation, PrintJobState, SignedAgentStateObservation,
    StructuredValue,
};
use crate::security::UnverifiedAgentStateEnvelope;
use crate::{GatewayError, GatewayResult};

pub(super) async fn reconcile_state_observation(
    transaction: &DatabaseTransaction,
    agent_id: &str,
    publication: &AgentPublication,
    now: DateTime<Utc>,
) -> GatewayResult<()> {
    let AgentPublication::RuntimeEvent { message_id, occurred_at, event, command_id, job_id } =
        publication
    else {
        return Ok(());
    };
    if event.resource_kind != "print_job" {
        return Ok(());
    }
    let Some(StructuredValue::Text(compact)) = event.payload.get("state_envelope") else {
        return Err(invalid("managed Print Job event has no signed state envelope"));
    };
    if event.payload.len() != 1 {
        return Err(invalid("managed Print Job event contains unsigned fields"));
    }
    let envelope = UnverifiedAgentStateEnvelope::parse(compact)?;
    let key = agent_verification_key::Entity::find_by_id(envelope.key_id())
        .filter(agent_verification_key::Column::AgentId.eq(agent_id))
        .filter(agent_verification_key::Column::Purpose.eq(AgentKeyPurpose::AgentState))
        .one(transaction)
        .await?
        .ok_or_else(|| invalid("Agent State key is not registered for this Agent"))?;
    let observation = envelope.verify(&key.public_jwk.0)?;
    let job = &observation.job;
    let expected_type = format!("print_job.{}", job.state.as_str());
    if observation.agent_id.as_str() != agent_id
        || observation.envelope_id != *message_id
        || job_id.as_deref() != Some(job.print_job_id.as_str())
        || event.resource_id != job.print_job_id
        || event.event_type != expected_type
        || event.sequence != observation.durable_state_sequence
        || *occurred_at != observation.observed_at
        || event.occurred_at
            != job
                .terminal_at
                .or(job.started_at)
                .unwrap_or(job.accepted_at)
    {
        return Err(invalid("publication does not match its signed Agent State"));
    }
    let command_id = command_id
        .as_deref()
        .ok_or_else(|| invalid("managed Print Job event has no dispatch command"))?;
    let dispatch = command::Entity::find_by_id(command_id)
        .filter(command::Column::AgentId.eq(agent_id))
        .lock_exclusive()
        .one(transaction)
        .await?
        .ok_or_else(|| invalid("managed Print Job dispatch does not belong to this Agent"))?;
    if !matches!(&dispatch.command.0, CommandContent::ManagedWork { managed_work_id } if managed_work_id == &job.managed_work_id)
    {
        return Err(invalid("managed Print Job does not match its dispatch command"));
    }
    let work = managed_work::Entity::find_by_id(job.managed_work_id.as_str())
        .lock_exclusive()
        .one(transaction)
        .await?
        .ok_or_else(|| invalid("managed Print Job has no Managed Work record"))?;
    validate_work_identity(&work, &observation)?;
    if !matches!(
        work.state,
        ManagedWorkStateValue::Dispatching
            | ManagedWorkStateValue::RecoveryUncertain
            | ManagedWorkStateValue::Accepted
    ) {
        return Err(invalid("Agent State conflicts with the Managed Work admission state"));
    }
    let replace_current = match &work.print_job_observation {
        Some(current) => reconcile_version(&current.0.observation, &observation)?,
        None => true,
    };
    let signed = SignedAgentStateObservation { observation, state_envelope: compact.clone() };
    agent_state_observation::Entity::insert(agent_state_observation::ActiveModel {
        envelope_id: Set(signed.observation.envelope_id.clone()),
        agent_id: Set(agent_id.to_owned()),
        managed_work_id: Set(work.managed_work_id.clone()),
        observation: Set(StoredAgentStateObservation(signed.clone())),
        received_at: Set(stored_time(now)),
    })
    .on_conflict(
        OnConflict::column(agent_state_observation::Column::EnvelopeId)
            .do_nothing()
            .to_owned(),
    )
    .try_insert()
    .exec(transaction)
    .await?;
    let stored = agent_state_observation::Entity::find_by_id(&signed.observation.envelope_id)
        .one(transaction)
        .await?
        .ok_or_else(|| invalid("Agent State observation was not stored"))?;
    if stored.agent_id != agent_id
        || stored.managed_work_id != work.managed_work_id
        || stored.observation.0 != signed
    {
        return Err(invalid("Agent State envelope ID was reused for different evidence"));
    }
    if replace_current {
        let mut update: managed_work::ActiveModel = work.clone().into();
        update.state = Set(ManagedWorkStateValue::Accepted);
        update.print_job_id = Set(Some(
            signed
                .observation
                .job
                .print_job_id
                .clone(),
        ));
        update.print_job_observation = Set(Some(StoredAgentStateObservation(signed)));
        update.error_code = Set(None);
        update.message_key = Set("managed_work.accepted".into());
        update.payload_deleted_at = Set(work
            .payload_deleted_at
            .or(Some(stored_time(now))));
        update.updated_at = Set(stored_time(now));
        update.update(transaction).await?;
    }
    let mut dispatch: command::ActiveModel = dispatch.into();
    dispatch.state = Set(CommandState::Accepted);
    dispatch.updated_at = Set(stored_time(now));
    dispatch.update(transaction).await?;
    managed_payload::Entity::delete_by_id(&work.managed_work_id)
        .exec(transaction)
        .await?;
    Ok(())
}

fn validate_work_identity(
    work: &managed_work::Model,
    observation: &AgentStateObservation,
) -> GatewayResult<()> {
    let job = &observation.job;
    let origin = &job.origin;
    if work.agent_id != observation.agent_id.as_str()
        || work.organization_id != origin.organization_id.as_str()
        || work.site_id != origin.site_id.as_str()
        || work.database_name != origin.database
        || work.company_id != origin.company_id
        || work.device_id != job.device_id.as_str()
        || work.print_intent_id != job.print_intent_id.as_str()
        || work.binding_claim.0.report_binding_id != origin.report_binding_id
        || work.binding_claim.0.report_action_id != origin.report_action
        || observation.payload_fingerprint
            != format!("sha256:{}", hex::encode(&work.payload_fingerprint))
        || work
            .print_job_id
            .as_ref()
            .is_some_and(|id| id != &job.print_job_id)
        || job.expires_at != utc_time(work.expires_at)
        || job.accepted_at > utc_time(work.expires_at)
    {
        return Err(invalid("Agent State does not match its Managed Work identity or deadline"));
    }
    Ok(())
}

fn reconcile_version(
    current: &AgentStateObservation,
    incoming: &AgentStateObservation,
) -> GatewayResult<bool> {
    let old = &current.job;
    let new = &incoming.job;
    if old.print_job_id != new.print_job_id
        || old.accepted_at != new.accepted_at
        || old.expires_at != new.expires_at
        || old.origin != new.origin
    {
        return Err(invalid("Agent State changed immutable Print Job metadata"));
    }
    if new.state_version < old.state_version {
        return Ok(false);
    }
    if new.state_version == old.state_version {
        if old != new {
            return Err(invalid("Agent State changed a recorded Print Job state version"));
        }
        return Ok(incoming.observed_at > current.observed_at);
    }
    if old.state.is_terminal()
        || new.state == PrintJobState::Accepted
        || (old.started_at.is_some() && old.started_at != new.started_at)
        || incoming.durable_state_sequence <= current.durable_state_sequence
    {
        return Err(invalid("Agent State conflicts with the recorded Print Job lifecycle"));
    }
    Ok(true)
}

fn invalid(detail: &str) -> GatewayError {
    GatewayError::Conflict(detail.to_owned())
}
