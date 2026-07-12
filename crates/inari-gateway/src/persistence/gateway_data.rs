use chrono::{DateTime, Utc};
use sqlx::Row;

use super::{
    AgentEnrollmentRecord, GatewayRepository, PersistedAgentStatus, PersistedCommand,
    PersistedPublication,
};
use crate::audit::{AuditAction, AuditEventDraft, AuditOutcome, AuditResource};
use crate::identity::ActorId;
use crate::protocol::{AgentPublication, ControllerCommand, GatewaySnapshot, JobId};
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn enroll_agent(
        &self,
        enrollment: AgentEnrollmentRecord,
        invitation_id: &str,
        snapshot: &GatewaySnapshot,
    ) -> GatewayResult<()> {
        let mut transaction = self.pool.begin().await?;
        let valid: i64 = sqlx::query_scalar(
            "SELECT COUNT(*) FROM invitations
             WHERE invitation_id = $1 AND state = 'claimed'
               AND bound_agent_id = $2 AND bound_key_id = $3",
        )
        .bind(invitation_id)
        .bind(&enrollment.agent_id)
        .bind(&enrollment.key_id)
        .fetch_one(&mut *transaction)
        .await?;
        if valid != 1 {
            return Err(GatewayError::Forbidden(
                "invitation is not claimed by this agent identity".into(),
            ));
        }

        sqlx::query(
            "INSERT INTO agents (
                agent_id, organization_id, site_id, key_id, jwk_thumbprint, public_jwk, certificate_pem,
                namespace, protocol_version, controller_actions, enrolled_at, last_enrolled_at
             ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
             ON CONFLICT(agent_id) DO UPDATE SET
                organization_id = excluded.organization_id,
                site_id = excluded.site_id,
                key_id = excluded.key_id,
                jwk_thumbprint = excluded.jwk_thumbprint,
                public_jwk = excluded.public_jwk,
                certificate_pem = excluded.certificate_pem,
                namespace = excluded.namespace,
                protocol_version = excluded.protocol_version,
                controller_actions = excluded.controller_actions,
                last_enrolled_at = excluded.last_enrolled_at",
        )
        .bind(&enrollment.agent_id)
        .bind(enrollment.organization_id.as_str())
        .bind(enrollment.site_id.as_str())
        .bind(&enrollment.key_id)
        .bind(&enrollment.jwk_thumbprint)
        .bind(serde_json::to_value(&enrollment.public_jwk)?)
        .bind(&enrollment.certificate_pem)
        .bind(&enrollment.namespace)
        .bind(enrollment.protocol_version.as_str())
        .bind(serde_json::to_value(&enrollment.controller_actions)?)
        .bind(enrollment.enrolled_at)
        .bind(enrollment.enrolled_at)
        .execute(&mut *transaction)
        .await?;

        sqlx::query(
            "UPDATE invitations
             SET state = 'enrolled', enrolled_at = $1, latest_snapshot = $2
             WHERE invitation_id = $3",
        )
        .bind(enrollment.enrolled_at)
        .bind(serde_json::to_value(snapshot)?)
        .bind(invitation_id)
        .execute(&mut *transaction)
        .await?;
        let agent_id = enrollment.agent_id.parse()?;
        super::audit::insert_audit_event(
            &mut transaction,
            &AuditEventDraft {
                organization_id: enrollment.organization_id,
                actor_id: ActorId::from_agent(&agent_id),
                action: AuditAction::AgentEnrolled,
                resource: AuditResource::Agent { agent_id },
                outcome: AuditOutcome::Succeeded,
                request_id: None,
            },
        )
        .await?;
        transaction.commit().await?;
        Ok(())
    }

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
        let mut transaction = self.pool.begin().await?;
        sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))")
            .bind(agent_id)
            .execute(&mut *transaction)
            .await?;
        if let Some(command_id) = requested_command_id {
            let existing = sqlx::query(
                "SELECT c.agent_id, a.namespace, c.command_id, c.message_id, c.sequence,
                        c.state, c.command, c.request_fingerprint, c.issued_at,
                        c.published_at, c.updated_at
                 FROM commands c
                 JOIN agents a ON a.agent_id = c.agent_id
                 WHERE c.command_id = $1",
            )
            .bind(command_id)
            .fetch_optional(&mut *transaction)
            .await?;
            if let Some(row) = existing {
                let stored_agent_id: String = row.try_get("agent_id")?;
                let stored_fingerprint: Vec<u8> = row.try_get("request_fingerprint")?;
                if stored_agent_id != agent_id
                    || stored_fingerprint.as_slice() != request_fingerprint
                {
                    return Err(GatewayError::Conflict(
                        "the idempotency key was already used for another request".into(),
                    ));
                }
                transaction.commit().await?;
                return persisted_command(&row);
            }
        }
        let namespace: String =
            sqlx::query_scalar("SELECT namespace FROM agents WHERE agent_id = $1")
                .bind(agent_id)
                .fetch_optional(&mut *transaction)
                .await?
                .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        let sequence: i64 = sqlx::query_scalar(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM commands WHERE agent_id = $1",
        )
        .bind(agent_id)
        .fetch_one(&mut *transaction)
        .await?;
        let sequence = u64::try_from(sequence)
            .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?;
        let command_id = requested_command_id
            .map(str::to_owned)
            .unwrap_or_else(|| format!("job_{agent_id}_{sequence}"));
        let message_id = format!("msg_{command_id}");
        let issued_at = Utc::now();
        let command = build(sequence, &command_id, &message_id, issued_at)?;
        sqlx::query(
            "INSERT INTO commands (
                command_id, agent_id, message_id, sequence, state, command,
                request_fingerprint, issued_at, updated_at
             ) VALUES ($1, $2, $3, $4, 'queued', $5, $6, $7, $8)",
        )
        .bind(&command_id)
        .bind(agent_id)
        .bind(&message_id)
        .bind(
            i64::try_from(sequence)
                .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?,
        )
        .bind(serde_json::to_value(&command)?)
        .bind(request_fingerprint.as_slice())
        .bind(issued_at)
        .bind(issued_at)
        .execute(&mut *transaction)
        .await?;
        transaction.commit().await?;
        Ok(PersistedCommand {
            agent_id: agent_id.into(),
            namespace,
            command_id,
            message_id,
            sequence,
            state: "queued".into(),
            command,
            issued_at,
            published_at: None,
            updated_at: issued_at,
        })
    }

    pub async fn mark_command_published(
        &self,
        agent_id: &str,
        command_id: &str,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        sqlx::query(
            "UPDATE commands SET state = 'published', published_at = $1, updated_at = $2
             WHERE agent_id = $3 AND command_id = $4",
        )
        .bind(now)
        .bind(now)
        .bind(agent_id)
        .bind(command_id)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn job(&self, job_id: &JobId) -> GatewayResult<PersistedCommand> {
        let row = sqlx::query(
            "SELECT c.agent_id, a.namespace, c.command_id, c.message_id, c.sequence,
                    c.state, c.command, c.issued_at, c.published_at, c.updated_at
             FROM commands c
             JOIN agents a ON a.agent_id = c.agent_id
             WHERE c.command_id = $1",
        )
        .bind(job_id.as_str())
        .fetch_optional(&self.pool)
        .await?
        .ok_or_else(|| GatewayError::NotFound("job was not found".into()))?;
        persisted_command(&row)
    }

    pub async fn jobs(&self, agent_id: &str) -> GatewayResult<Vec<PersistedCommand>> {
        let rows = sqlx::query(
            "SELECT c.agent_id, a.namespace, c.command_id, c.message_id, c.sequence,
                    c.state, c.command, c.issued_at, c.published_at, c.updated_at
             FROM commands c
             JOIN agents a ON a.agent_id = c.agent_id
             WHERE c.agent_id = $1
             ORDER BY c.issued_at DESC",
        )
        .bind(agent_id)
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(persisted_command)
            .collect()
    }

    pub async fn command_history(
        &self,
        agent_id: &str,
        from_sequence: u64,
    ) -> GatewayResult<(crate::protocol::ProtocolVersion, Vec<ControllerCommand>)> {
        let protocol_version: String =
            sqlx::query_scalar("SELECT protocol_version FROM agents WHERE agent_id = $1")
                .bind(agent_id)
                .fetch_optional(&self.pool)
                .await?
                .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        let rows = sqlx::query(
            "SELECT command FROM commands
             WHERE agent_id = $1 AND sequence >= $2 ORDER BY sequence",
        )
        .bind(agent_id)
        .bind(i64::try_from(from_sequence).unwrap_or(i64::MAX))
        .fetch_all(&self.pool)
        .await?;
        let commands = rows
            .iter()
            .map(|row| serde_json::from_value(row.try_get("command")?).map_err(Into::into))
            .collect::<GatewayResult<Vec<_>>>()?;
        Ok((protocol_version.parse()?, commands))
    }

    pub async fn record_publication(
        &self,
        agent_id: &str,
        key: &str,
        message: &AgentPublication,
        now: DateTime<Utc>,
    ) -> GatewayResult<()> {
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = $1")
            .bind(agent_id)
            .fetch_one(&self.pool)
            .await?;
        if exists == 0 {
            return Ok(());
        }
        let message_id = message.message_id();
        let message_type = message.message_type();
        let mut transaction = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO publications
                (message_id, agent_id, key_expr, message_type, payload, received_at)
             VALUES ($1, $2, $3, $4, $5, $6)
             ON CONFLICT(message_id) DO UPDATE SET
                key_expr = excluded.key_expr,
                message_type = excluded.message_type,
                payload = excluded.payload,
                received_at = excluded.received_at",
        )
        .bind(message_id)
        .bind(agent_id)
        .bind(key)
        .bind(message_type)
        .bind(serde_json::to_value(message)?)
        .bind(now)
        .execute(&mut *transaction)
        .await?;
        if let Some(snapshot) = message.snapshot() {
            sqlx::query(
                "UPDATE invitations
                 SET state = 'online', online_at = COALESCE(online_at, $1), latest_snapshot = $2
                 WHERE bound_agent_id = $3 AND state IN ('claimed', 'enrolled', 'online')",
            )
            .bind(now)
            .bind(serde_json::to_value(snapshot)?)
            .bind(agent_id)
            .execute(&mut *transaction)
            .await?;
        }
        if let Some(command_id) = message.command_id() {
            let state = match message_type {
                "agent.command.accepted" => Some("accepted"),
                "agent.command.rejected" => Some("rejected"),
                _ => None,
            };
            if let Some(state) = state {
                sqlx::query(
                    "UPDATE commands SET state = $1, updated_at = $2
                     WHERE agent_id = $3 AND command_id = $4",
                )
                .bind(state)
                .bind(now)
                .bind(agent_id)
                .bind(command_id)
                .execute(&mut *transaction)
                .await?;
            }
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn publications(&self, agent_id: &str) -> GatewayResult<Vec<PersistedPublication>> {
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = $1")
            .bind(agent_id)
            .fetch_one(&self.pool)
            .await?;
        if exists == 0 {
            return Err(GatewayError::NotFound("unknown managed gateway agent".into()));
        }
        let rows = sqlx::query(
            "SELECT key_expr, message_id, message_type, received_at, payload
             FROM publications WHERE agent_id = $1 ORDER BY received_at DESC",
        )
        .bind(agent_id)
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                Ok(PersistedPublication {
                    key: row.try_get("key_expr")?,
                    received_at: row.try_get("received_at")?,
                    message: serde_json::from_value(row.try_get("payload")?)?,
                })
            })
            .collect()
    }

    pub async fn latest_status(
        &self,
        agent_id: &str,
    ) -> GatewayResult<Option<PersistedAgentStatus>> {
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = $1")
            .bind(agent_id)
            .fetch_one(&self.pool)
            .await?;
        if exists == 0 {
            return Err(GatewayError::NotFound("unknown managed gateway agent".into()));
        }

        let row = sqlx::query(
            "SELECT payload, received_at
             FROM publications
             WHERE agent_id = $1 AND message_type = 'agent.status.snapshot'
             ORDER BY received_at DESC
             LIMIT 1",
        )
        .bind(agent_id)
        .fetch_optional(&self.pool)
        .await?;

        row.map(|row| {
            let message = serde_json::from_value::<AgentPublication>(row.try_get("payload")?)?;
            let AgentPublication::StatusSnapshot { message_id, snapshot } = message else {
                return Err(GatewayError::CorruptState(
                    "status publication row contains another message type".into(),
                ));
            };
            Ok(PersistedAgentStatus {
                message_id,
                received_at: row.try_get("received_at")?,
                snapshot: *snapshot,
            })
        })
        .transpose()
    }
}

fn persisted_command(row: &sqlx::postgres::PgRow) -> GatewayResult<PersistedCommand> {
    let sequence = row.try_get::<i64, _>("sequence")?;
    Ok(PersistedCommand {
        agent_id: row.try_get("agent_id")?,
        namespace: row.try_get("namespace")?,
        command_id: row.try_get("command_id")?,
        message_id: row.try_get("message_id")?,
        sequence: u64::try_from(sequence)
            .map_err(|_| GatewayError::CorruptState("stored command sequence is invalid".into()))?,
        state: row.try_get("state")?,
        command: serde_json::from_value(row.try_get("command")?)?,
        issued_at: row.try_get("issued_at")?,
        published_at: row.try_get("published_at")?,
        updated_at: row.try_get("updated_at")?,
    })
}
