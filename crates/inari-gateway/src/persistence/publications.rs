use chrono::{DateTime, Utc};
use sea_orm::sea_query::{Expr, OnConflict};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, EntityTrait, QueryFilter, QueryOrder,
    TransactionTrait,
};

use super::entity::value::{
    CommandState, InvitationState, PublicationType, StoredPublication, StoredSnapshot,
};
use super::entity::{agent, command, invitation, publication};
use super::{
    GatewayRepository, PersistedAgentStatus, PersistedPublication, require_agent, stored_time,
    utc_time,
};
use crate::protocol::AgentPublication;
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
