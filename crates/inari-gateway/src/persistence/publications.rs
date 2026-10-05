use chrono::{DateTime, Utc};
use sea_orm::sea_query::{Expr, OnConflict};
use sea_orm::{
    ActiveModelTrait, ActiveValue::Set, ColumnTrait, EntityTrait, QueryFilter, QueryOrder,
    QuerySelect, TransactionTrait, TryInsertResult,
};

use super::entity::value::{
    CommandState, InvitationState, PublicationType, StoredPublication, StoredSnapshot,
};
use super::entity::{agent, command, invitation, publication};
use super::{
    CommandContent, GatewayRepository, PersistedAgentStatus, PersistedPublication, require_agent,
    stored_time, utc_time,
};
use crate::audit::{AuditAction, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
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
        let result = self
            .record_publication_transaction(agent_id, key, message, now)
            .await;
        if message.snapshot().is_some()
            && matches!(&result, Err(GatewayError::InvalidInput(_) | GatewayError::Conflict(_)))
        {
            let agent = require_agent(&self.database, agent_id).await?;
            let agent_id = agent.agent_id.parse()?;
            self.record_audit_event(&AuditEventDraft {
                organization_id: agent.organization_id.parse()?,
                actor_id: ActorId::from_agent(&agent_id),
                action: AuditAction::AgentInventoryRejected,
                resource: AuditResource::Agent { agent_id },
                outcome: AuditOutcome::Denied,
                request_id: None,
            })
            .await?;
        }
        result
    }

    async fn record_publication_transaction(
        &self,
        agent_id: &str,
        key: &str,
        message: &AgentPublication,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        let inventory = message
            .snapshot()
            .map(|snapshot| {
                super::inventory::InventoryProjection::parse(&snapshot.runtime.inventory)
            })
            .transpose()?;
        let transaction = self.database.begin().await?;
        let candidate = require_agent(&transaction, agent_id).await?;
        // The invitation precedes the Agent lock, as it does during enrollment.
        // The Agent is read again under lock because another invitation can rotate it.
        let bound_invitation = if message.snapshot().is_some() {
            invitation::Entity::find()
                .filter(invitation::Column::BoundAgentId.eq(agent_id))
                .filter(invitation::Column::EnrolledAt.eq(candidate.last_enrolled_at))
                .filter(
                    invitation::Column::State
                        .is_in([InvitationState::Enrolled, InvitationState::Online]),
                )
                .lock_exclusive()
                .one(&transaction)
                .await?
        } else {
            None
        };
        let current_agent = agent::Entity::find_by_id(agent_id)
            .lock_shared()
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::NotFound(format!("Agent {agent_id} is not enrolled")))?;
        let inserted = publication::Entity::insert(publication::ActiveModel {
            message_id: Set(message.message_id().to_owned()),
            agent_id: Set(agent_id.to_owned()),
            key_expr: Set(key.to_owned()),
            message_type: Set(Some(PublicationType::from_message(message))),
            payload: Set(StoredPublication(message.clone())),
            received_at: Set(stored_time(now)),
        })
        .on_conflict(
            OnConflict::column(publication::COLUMN.message_id)
                .do_nothing()
                .to_owned(),
        )
        .try_insert()
        .exec(&transaction)
        .await?;
        let stored = publication::Entity::find_by_id(message.message_id())
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::CorruptState("publication was not stored".into()))?;
        if stored.agent_id != agent_id || stored.payload.0 != *message {
            return Err(GatewayError::Conflict(
                "publication message ID was reused for different content".into(),
            ));
        }
        if !matches!(inserted, TryInsertResult::Inserted(_)) {
            transaction.commit().await?;
            return Ok(());
        }
        super::state_observations::reconcile_state_observation(
            &transaction,
            agent_id,
            message,
            now,
        )
        .await?;
        if let (Some(snapshot), Some(inventory), Some(model)) =
            (message.snapshot(), inventory, bound_invitation)
            && model.enrolled_at == Some(current_agent.last_enrolled_at)
            && model.bound_key_id.as_deref() == Some(current_agent.key_id.as_str())
            && model.site_id == current_agent.site_id
            && model.organization_id == current_agent.organization_id
            && model
                .latest_snapshot
                .as_ref()
                .is_none_or(|previous| snapshot.generated_at > previous.0.generated_at)
        {
            inventory
                .apply(&transaction, agent_id, &current_agent.site_id, snapshot.generated_at)
                .await?;
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
            // Managed Work needs signed Agent State before a receipt can stop dispatch.
            if let Some(state) = state
                && let Some(dispatch) = command::Entity::find_by_id(command_id)
                    .filter(command::COLUMN.agent_id.eq(agent_id))
                    .one(&transaction)
                    .await?
                && matches!(dispatch.command.0, CommandContent::Inline(_))
            {
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
            // Observation time alone cannot identify the applied snapshot when times tie.
            .filter(Expr::cust(
                "EXISTS (
                    SELECT 1 FROM invitations
                    JOIN agents ON agents.agent_id = invitations.bound_agent_id
                    WHERE agents.agent_id = publications.agent_id
                      AND invitations.enrolled_at = agents.last_enrolled_at
                      AND invitations.bound_key_id = agents.key_id
                      AND invitations.organization_id = agents.organization_id
                      AND invitations.site_id = agents.site_id
                      AND invitations.state IN ('enrolled', 'online')
                      AND publications.received_at >= agents.last_enrolled_at
                      AND invitations.latest_snapshot = publications.payload->'snapshot'
                )",
            ))
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
