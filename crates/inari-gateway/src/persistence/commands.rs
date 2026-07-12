use chrono::{DateTime, Utc};
use sea_orm::sea_query::Expr;
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, ConnectionTrait, DbBackend, EntityTrait,
    FromQueryResult, QueryFilter, QueryOrder, QuerySelect, Statement, TransactionTrait,
};

use super::entity::value::{CommandState, StoredCommand};
use super::entity::{agent, command};
use super::{GatewayRepository, PersistedCommand, require_agent, stored_time, utc_time};
use crate::protocol::{ControllerCommand, JobId, JobState};
use crate::{GatewayError, GatewayResult};

#[derive(Debug, FromQueryResult)]
struct NextSequence {
    max_sequence: Option<i64>,
}

impl GatewayRepository {
    pub async fn enqueue_command<F>(
        &self,
        agent_id: &str,
        requested_command_id: Option<&str>,
        request_fingerprint: &[u8; 32],
        build: F,
    ) -> GatewayResult<PersistedCommand>
    where
        F: FnOnce(u64, &str, &str, DateTime<Utc>) -> GatewayResult<ControllerCommand>,
    {
        let transaction = self.database.begin().await?;
        transaction
            .execute_raw(Statement::from_sql_and_values(
                DbBackend::Postgres,
                "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                [agent_id.to_owned().into()],
            ))
            .await?;
        let managed_agent = agent::Entity::find_by_id(agent_id)
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        if let Some(command_id) = requested_command_id
            && let Some(existing) = command::Entity::find_by_id(command_id)
                .one(&transaction)
                .await?
        {
            if existing.agent_id != agent_id
                || existing.request_fingerprint.as_slice() != request_fingerprint
            {
                return Err(GatewayError::Conflict(
                    "the idempotency key was already used for another request".into(),
                ));
            }
            transaction.commit().await?;
            return persisted_command(existing, managed_agent.namespace);
        }

        let next = command::Entity::find()
            .select_only()
            .column_as(command::COLUMN.sequence.0.max(), "max_sequence")
            .filter(command::COLUMN.agent_id.eq(agent_id))
            .into_model::<NextSequence>()
            .one(&transaction)
            .await?
            .ok_or_else(|| {
                GatewayError::CorruptState("command sequence query returned no row".into())
            })?
            .max_sequence
            .unwrap_or(0)
            .checked_add(1)
            .ok_or_else(|| GatewayError::Conflict("command sequence is out of range".into()))?;
        let sequence = u64::try_from(next)
            .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?;
        let command_id = requested_command_id
            .map(str::to_owned)
            .unwrap_or_else(|| format!("job_{agent_id}_{sequence}"));
        let message_id = format!("msg_{command_id}");
        let issued_at = Utc::now();
        let command_message = build(sequence, &command_id, &message_id, issued_at)?;
        let model = command::ActiveModel {
            command_id: Set(command_id),
            agent_id: Set(agent_id.to_owned()),
            message_id: Set(message_id),
            sequence: Set(next),
            state: Set(CommandState::Queued),
            command: Set(StoredCommand(command_message)),
            request_fingerprint: Set(request_fingerprint.to_vec()),
            issued_at: Set(stored_time(issued_at)),
            published_at: Set(None),
            updated_at: Set(stored_time(issued_at)),
        }
        .insert(&transaction)
        .await?;
        transaction.commit().await?;
        persisted_command(model, managed_agent.namespace)
    }

    pub async fn mark_command_published(
        &self,
        agent_id: &str,
        command_id: &str,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        command::Entity::update_many()
            .col_expr(command::COLUMN.state, Expr::value(CommandState::Published))
            .col_expr(command::COLUMN.published_at, Expr::value(stored_time(now)))
            .col_expr(command::COLUMN.updated_at, Expr::value(stored_time(now)))
            .filter(command::COLUMN.agent_id.eq(agent_id))
            .filter(
                command::COLUMN
                    .command_id
                    .eq(command_id),
            )
            .exec(&self.database)
            .await?;
        Ok(())
    }

    pub async fn job(&self, job_id: &JobId) -> GatewayResult<PersistedCommand> {
        let model = command::Entity::find_by_id(job_id.as_str())
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("job was not found".into()))?;
        let namespace = agent_namespace(&self.database, &model.agent_id).await?;
        persisted_command(model, namespace)
    }

    pub async fn jobs(&self, agent_id: &str) -> GatewayResult<Vec<PersistedCommand>> {
        let namespace = agent_namespace(&self.database, agent_id).await?;
        command::Entity::find()
            .filter(command::COLUMN.agent_id.eq(agent_id))
            .order_by_desc(command::COLUMN.issued_at)
            .all(&self.database)
            .await?
            .into_iter()
            .map(|model| persisted_command(model, namespace.clone()))
            .collect()
    }

    pub async fn command_history(
        &self,
        agent_id: &str,
        from_sequence: u64,
    ) -> GatewayResult<(crate::protocol::ProtocolVersion, Vec<ControllerCommand>)> {
        let managed_agent = agent::Entity::find_by_id(agent_id)
            .one(&self.database)
            .await?
            .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        let commands = command::Entity::find()
            .filter(command::COLUMN.agent_id.eq(agent_id))
            .filter(
                command::COLUMN
                    .sequence
                    .gte(i64::try_from(from_sequence).unwrap_or(i64::MAX)),
            )
            .order_by_asc(command::COLUMN.sequence)
            .all(&self.database)
            .await?
            .into_iter()
            .map(|model| model.command.0)
            .collect();
        Ok((managed_agent.protocol_version.parse()?, commands))
    }
}

async fn agent_namespace<C>(database: &C, agent_id: &str) -> GatewayResult<String>
where
    C: ConnectionTrait,
{
    Ok(require_agent(database, agent_id)
        .await?
        .namespace)
}

fn persisted_command(model: command::Model, namespace: String) -> GatewayResult<PersistedCommand> {
    Ok(PersistedCommand {
        agent_id: model.agent_id.parse()?,
        namespace,
        command_id: model.command_id.parse()?,
        message_id: model.message_id,
        sequence: u64::try_from(model.sequence)
            .map_err(|_| GatewayError::CorruptState("stored command sequence is invalid".into()))?,
        state: model.state.into(),
        command: model.command.0,
        issued_at: utc_time(model.issued_at),
        published_at: model.published_at.map(utc_time),
        updated_at: utc_time(model.updated_at),
    })
}

impl From<CommandState> for JobState {
    fn from(state: CommandState) -> Self {
        match state {
            CommandState::Queued => Self::Queued,
            CommandState::Published => Self::Published,
            CommandState::Accepted => Self::Accepted,
            CommandState::Rejected => Self::Rejected,
            CommandState::Completed => Self::Completed,
            CommandState::Failed => Self::Failed,
            CommandState::Superseded => Self::Superseded,
        }
    }
}
