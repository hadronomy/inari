use chrono::{DateTime, Utc};
use sea_orm::sea_query::{Expr, OnConflict};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, EntityTrait, QueryFilter, QueryOrder,
    QuerySelect, TransactionTrait,
};

use super::entity::value::{
    CommandState, InvitationState, ManagedWorkStateValue, PublicationType, StoredPublication,
    StoredSnapshot,
};
use super::entity::{agent, command, invitation, managed_payload, managed_work, publication};
use super::{
    CommandContent, GatewayRepository, PersistedAgentStatus, PersistedPublication, require_agent,
    stored_time, utc_time,
};
use crate::protocol::{AgentPublication, StructuredFields, StructuredValue};
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn record_publication(
        &self,
        agent_id: &str,
        key: &str,
        message: &AgentPublication,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        if agent::Entity::find_by_id(agent_id)
            .one(&self.database)
            .await?
            .is_none()
        {
            return Ok(());
        }
        let transaction = self.database.begin().await?;
        publication::Entity::insert(publication::ActiveModel {
            message_id: Set(message.message_id().to_owned()),
            agent_id: Set(agent_id.to_owned()),
            key_expr: Set(key.to_owned()),
            message_type: Set(Some(PublicationType::from_message(message))),
            payload: Set(StoredPublication(message.clone())),
            received_at: Set(stored_time(now)),
        })
        .on_conflict(
            OnConflict::column(publication::COLUMN.message_id)
                .update_column(publication::COLUMN.key_expr)
                .update_column(publication::COLUMN.message_type)
                .update_column(publication::COLUMN.payload)
                .update_column(publication::COLUMN.received_at)
                .to_owned(),
        )
        .exec(&transaction)
        .await?;
        if let Some(snapshot) = message.snapshot()
            && let Some(model) = invitation::Entity::find()
                .filter(
                    invitation::COLUMN
                        .bound_agent_id
                        .eq(agent_id),
                )
                .filter(invitation::COLUMN.state.is_in([
                    InvitationState::Claimed,
                    InvitationState::Enrolled,
                    InvitationState::Online,
                ]))
                .one(&transaction)
                .await?
        {
            let mut update: invitation::ActiveModel = model.into();
            update.state = Set(InvitationState::Online);
            if update.online_at.as_ref().is_none() {
                update.online_at = Set(Some(stored_time(now)));
            }
            update.latest_snapshot = Set(Some(StoredSnapshot(snapshot.clone())));
            update.update(&transaction).await?;
        }
        if let Some(command_id) = message.command_id() {
            let state = match message {
                AgentPublication::CommandAccepted { .. } => Some(CommandState::Accepted),
                AgentPublication::CommandRejected { .. } => Some(CommandState::Rejected),
                _ => None,
            };
            if let Some(state) = state {
                command::Entity::update_many()
                    .col_expr(command::COLUMN.state, Expr::value(state))
                    .col_expr(command::COLUMN.updated_at, Expr::value(stored_time(now)))
                    .filter(command::COLUMN.agent_id.eq(agent_id))
                    .filter(
                        command::COLUMN
                            .command_id
                            .eq(command_id),
                    )
                    .exec(&transaction)
                    .await?;
                reconcile_managed_dispatch(&transaction, agent_id, command_id, message, now)
                    .await?;
            }
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn publications(&self, agent_id: &str) -> GatewayResult<Vec<PersistedPublication>> {
        require_agent(&self.database, agent_id).await?;
        Ok(publication::Entity::find()
            .filter(
                publication::COLUMN
                    .agent_id
                    .eq(agent_id),
            )
            .order_by_desc(publication::COLUMN.received_at)
            .all(&self.database)
            .await?
            .into_iter()
            .map(|model| PersistedPublication {
                key: model.key_expr,
                received_at: utc_time(model.received_at),
                message: model.payload.0,
            })
            .collect())
    }

    pub async fn latest_status(
        &self,
        agent_id: &str,
    ) -> GatewayResult<Option<PersistedAgentStatus>> {
        require_agent(&self.database, agent_id).await?;
        publication::Entity::find()
            .filter(
                publication::COLUMN
                    .agent_id
                    .eq(agent_id),
            )
            .filter(
                publication::COLUMN
                    .message_type
                    .eq(PublicationType::StatusSnapshot),
            )
            .order_by_desc(publication::COLUMN.received_at)
            .one(&self.database)
            .await?
            .map(|model| {
                let AgentPublication::StatusSnapshot { message_id, snapshot } = model.payload.0
                else {
                    return Err(GatewayError::CorruptState(
                        "status publication row contains another message type".into(),
                    ));
                };
                Ok(PersistedAgentStatus {
                    message_id,
                    received_at: utc_time(model.received_at),
                    snapshot: *snapshot,
                })
            })
            .transpose()
    }
}

async fn reconcile_managed_dispatch(
    transaction: &sea_orm::DatabaseTransaction,
    agent_id: &str,
    command_id: &str,
    message: &AgentPublication,
    now: DateTime<Utc>,
) -> GatewayResult<()> {
    let Some(command) = command::Entity::find_by_id(command_id)
        .filter(command::Column::AgentId.eq(agent_id))
        .one(transaction)
        .await?
    else {
        return Ok(());
    };
    let CommandContent::ManagedWork { managed_work_id } = command.command.0 else {
        return Ok(());
    };
    let Some(work) = managed_work::Entity::find_by_id(managed_work_id.as_str())
        .lock_exclusive()
        .one(transaction)
        .await?
    else {
        return Err(GatewayError::CorruptState("Managed Work dispatch metadata is missing".into()));
    };
    let (state, print_job_id, error_code, message_key) = match message {
        AgentPublication::CommandAccepted { job, accepted_at, .. } => {
            let print_job_id = accepted_print_job_id(job.as_ref(), &work)?;
            if *accepted_at > utc_time(work.expires_at) {
                return Err(GatewayError::Conflict(
                    "Agent acceptance is later than the Managed Work deadline".into(),
                ));
            }
            if work.state == ManagedWorkStateValue::Accepted
                && work.print_job_id.as_ref() == Some(&print_job_id)
            {
                return Ok(());
            }
            if !matches!(
                work.state,
                ManagedWorkStateValue::Dispatching | ManagedWorkStateValue::RecoveryUncertain
            ) {
                return Err(GatewayError::Conflict(
                    "Agent acceptance conflicts with the Managed Work state".into(),
                ));
            }
            (
                ManagedWorkStateValue::Accepted,
                Some(print_job_id),
                None,
                "managed_work.accepted".to_owned(),
            )
        },
        AgentPublication::CommandRejected { code, .. } => {
            if work.state == ManagedWorkStateValue::Rejected
                && work.error_code.as_ref() == Some(code)
            {
                return Ok(());
            }
            if work.state != ManagedWorkStateValue::Dispatching {
                return Err(GatewayError::Conflict(
                    "Agent rejection conflicts with the Managed Work state".into(),
                ));
            }
            (
                ManagedWorkStateValue::Rejected,
                None,
                Some(code.clone()),
                "managed_work.rejected".to_owned(),
            )
        },
        _ => return Ok(()),
    };
    let mut update: managed_work::ActiveModel = work.into();
    update.state = Set(state);
    update.print_job_id = Set(print_job_id);
    update.error_code = Set(error_code);
    update.message_key = Set(message_key);
    update.payload_deleted_at = Set(Some(stored_time(now)));
    update.updated_at = Set(stored_time(now));
    update.update(transaction).await?;
    managed_payload::Entity::delete_by_id(managed_work_id.as_str())
        .exec(transaction)
        .await?;
    Ok(())
}

fn accepted_print_job_id(
    job: Option<&StructuredFields>,
    work: &managed_work::Model,
) -> GatewayResult<String> {
    let job = job.ok_or_else(|| {
        GatewayError::InvalidInput("Managed Work acceptance has no Print Job identity".into())
    })?;
    for (field, expected) in [
        ("managed_work_id", work.managed_work_id.as_str()),
        ("print_intent_id", work.print_intent_id.as_str()),
        ("device_id", work.device_id.as_str()),
        ("state", "accepted"),
    ] {
        if !matches!(job.get(field), Some(StructuredValue::Text(value)) if value == expected) {
            return Err(GatewayError::Conflict(format!(
                "Managed Work acceptance does not match its {field}"
            )));
        }
    }
    if !matches!(
        job.get("state_version"),
        Some(StructuredValue::Unsigned(1..) | StructuredValue::Signed(1..))
    ) || !matches!(job.get("replayed"), Some(StructuredValue::Boolean(_)))
    {
        return Err(GatewayError::InvalidInput(
            "Managed Work acceptance has an invalid receipt version or replay flag".into(),
        ));
    }
    match job.get("print_job_id") {
        Some(StructuredValue::Text(value))
            if value.len() <= 128
                && value
                    .as_bytes()
                    .first()
                    .is_some_and(u8::is_ascii_alphanumeric)
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._:/-".contains(&byte)) =>
        {
            Ok(value.clone())
        },
        _ => Err(GatewayError::InvalidInput(
            "Managed Work acceptance has an invalid Print Job identity".into(),
        )),
    }
}
