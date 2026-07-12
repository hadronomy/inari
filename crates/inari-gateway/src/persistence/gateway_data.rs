use chrono::{DateTime, Utc};
use serde_json::Value;
use sqlx::Row;

use super::{
    AgentEnrollmentRecord, EnrollmentCredential, GatewayRepository, PersistedAgentStatus,
    PersistedCommand, PersistedPublication,
};
use crate::protocol::AgentPublication;
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn enroll_agent(
        &self,
        enrollment: AgentEnrollmentRecord,
        credential: EnrollmentCredential,
        snapshot: &Value,
    ) -> GatewayResult<()> {
        let mut transaction = self.pool.begin().await?;
        match &credential {
            EnrollmentCredential::ConfiguredToken { digest } => {
                if let Some(row) = sqlx::query(
                    "SELECT agent_id, key_id FROM consumed_enrollment_tokens WHERE token_digest = ?",
                )
                .bind(digest.as_slice())
                .fetch_optional(&mut *transaction)
                .await?
                {
                    let agent_id: String = row.try_get("agent_id")?;
                    let key_id: String = row.try_get("key_id")?;
                    if agent_id != enrollment.agent_id || key_id != enrollment.key_id {
                        return Err(GatewayError::Forbidden(
                            "enrollment token has already been consumed by another identity".into(),
                        ));
                    }
                }
            },
            EnrollmentCredential::Invitation { invitation_id } => {
                let valid: i64 = sqlx::query_scalar(
                    "SELECT COUNT(*) FROM invitations
                     WHERE invitation_id = ? AND state = 'claimed'
                       AND bound_agent_id = ? AND bound_key_id = ?",
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
            },
        }

        sqlx::query(
            "INSERT INTO agents (
                agent_id, key_id, jwk_thumbprint, public_jwk_json, certificate_pem,
                namespace, protocol_version, controller_actions_json, enrolled_at, last_enrolled_at
             ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(agent_id) DO UPDATE SET
                key_id = excluded.key_id,
                jwk_thumbprint = excluded.jwk_thumbprint,
                public_jwk_json = excluded.public_jwk_json,
                certificate_pem = excluded.certificate_pem,
                namespace = excluded.namespace,
                protocol_version = excluded.protocol_version,
                controller_actions_json = excluded.controller_actions_json,
                last_enrolled_at = excluded.last_enrolled_at",
        )
        .bind(&enrollment.agent_id)
        .bind(&enrollment.key_id)
        .bind(&enrollment.jwk_thumbprint)
        .bind(serde_json::to_string(&enrollment.public_jwk)?)
        .bind(&enrollment.certificate_pem)
        .bind(&enrollment.namespace)
        .bind(enrollment.protocol_version.as_str())
        .bind(serde_json::to_string(&enrollment.controller_actions)?)
        .bind(enrollment.enrolled_at)
        .bind(enrollment.enrolled_at)
        .execute(&mut *transaction)
        .await?;

        match credential {
            EnrollmentCredential::ConfiguredToken { digest } => {
                sqlx::query(
                    "INSERT INTO consumed_enrollment_tokens
                        (token_digest, agent_id, key_id, consumed_at)
                     VALUES (?, ?, ?, ?)
                     ON CONFLICT(token_digest) DO UPDATE SET consumed_at = excluded.consumed_at",
                )
                .bind(digest.as_slice())
                .bind(&enrollment.agent_id)
                .bind(&enrollment.key_id)
                .bind(enrollment.enrolled_at)
                .execute(&mut *transaction)
                .await?;
            },
            EnrollmentCredential::Invitation { invitation_id } => {
                sqlx::query(
                    "UPDATE invitations
                     SET state = 'enrolled', enrolled_at = ?, latest_snapshot_json = ?
                     WHERE invitation_id = ?",
                )
                .bind(enrollment.enrolled_at)
                .bind(serde_json::to_string(snapshot)?)
                .bind(invitation_id)
                .execute(&mut *transaction)
                .await?;
            },
        }
        transaction.commit().await?;
        Ok(())
    }

    pub async fn enqueue_command<F>(
        &self,
        agent_id: &str,
        requested_command_id: Option<&str>,
        build: F,
    ) -> GatewayResult<PersistedCommand>
    where
        F: FnOnce(u64, &str, &str, DateTime<Utc>) -> GatewayResult<Value>,
    {
        let mut transaction = self.pool.begin().await?;
        let namespace: String =
            sqlx::query_scalar("SELECT namespace FROM agents WHERE agent_id = ?")
                .bind(agent_id)
                .fetch_optional(&mut *transaction)
                .await?
                .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        let sequence: i64 = sqlx::query_scalar(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM commands WHERE agent_id = ?",
        )
        .bind(agent_id)
        .fetch_one(&mut *transaction)
        .await?;
        let sequence = u64::try_from(sequence)
            .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?;
        let command_id = requested_command_id
            .map(str::to_owned)
            .unwrap_or_else(|| format!("cmd_{agent_id}_{sequence}"));
        let message_id = format!("msg_{agent_id}_{sequence}");
        let issued_at = Utc::now();
        let command = build(sequence, &command_id, &message_id, issued_at)?;
        sqlx::query(
            "INSERT INTO commands (
                command_id, agent_id, message_id, sequence, state, command_json,
                issued_at, updated_at
             ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
        )
        .bind(&command_id)
        .bind(agent_id)
        .bind(&message_id)
        .bind(
            i64::try_from(sequence)
                .map_err(|_| GatewayError::Conflict("command sequence is out of range".into()))?,
        )
        .bind(serde_json::to_string(&command)?)
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
            "UPDATE commands SET state = 'published', published_at = ?, updated_at = ?
             WHERE agent_id = ? AND command_id = ?",
        )
        .bind(now)
        .bind(now)
        .bind(agent_id)
        .bind(command_id)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn command_history(
        &self,
        agent_id: &str,
        from_sequence: u64,
    ) -> GatewayResult<(crate::protocol::ProtocolVersion, Vec<Value>)> {
        let protocol_version: String =
            sqlx::query_scalar("SELECT protocol_version FROM agents WHERE agent_id = ?")
                .bind(agent_id)
                .fetch_optional(&self.pool)
                .await?
                .ok_or_else(|| GatewayError::NotFound("unknown managed gateway agent".into()))?;
        let rows = sqlx::query(
            "SELECT command_json FROM commands
             WHERE agent_id = ? AND sequence >= ? ORDER BY sequence",
        )
        .bind(agent_id)
        .bind(i64::try_from(from_sequence).unwrap_or(i64::MAX))
        .fetch_all(&self.pool)
        .await?;
        let commands = rows
            .iter()
            .map(|row| {
                let raw: String = row.try_get("command_json")?;
                Ok(serde_json::from_str(&raw)?)
            })
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
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = ?")
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
                (message_id, agent_id, key_expr, message_type, payload_json, received_at)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(message_id) DO UPDATE SET
                key_expr = excluded.key_expr,
                message_type = excluded.message_type,
                payload_json = excluded.payload_json,
                received_at = excluded.received_at",
        )
        .bind(message_id)
        .bind(agent_id)
        .bind(key)
        .bind(message_type)
        .bind(serde_json::to_string(message)?)
        .bind(now)
        .execute(&mut *transaction)
        .await?;
        if let Some(snapshot) = message.snapshot() {
            sqlx::query(
                "UPDATE invitations
                 SET state = 'online', online_at = COALESCE(online_at, ?), latest_snapshot_json = ?
                 WHERE bound_agent_id = ? AND state IN ('claimed', 'enrolled', 'online')",
            )
            .bind(now)
            .bind(serde_json::to_string(snapshot)?)
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
                    "UPDATE commands SET state = ?, updated_at = ?
                     WHERE agent_id = ? AND command_id = ?",
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
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = ?")
            .bind(agent_id)
            .fetch_one(&self.pool)
            .await?;
        if exists == 0 {
            return Err(GatewayError::NotFound("unknown managed gateway agent".into()));
        }
        let rows = sqlx::query(
            "SELECT key_expr, message_id, message_type, received_at, payload_json
             FROM publications WHERE agent_id = ? ORDER BY received_at DESC",
        )
        .bind(agent_id)
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                let raw: String = row.try_get("payload_json")?;
                Ok(PersistedPublication {
                    key: row.try_get("key_expr")?,
                    received_at: row.try_get("received_at")?,
                    message: serde_json::from_str(&raw)?,
                })
            })
            .collect()
    }

    pub async fn latest_status(
        &self,
        agent_id: &str,
    ) -> GatewayResult<Option<PersistedAgentStatus>> {
        let exists: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM agents WHERE agent_id = ?")
            .bind(agent_id)
            .fetch_one(&self.pool)
            .await?;
        if exists == 0 {
            return Err(GatewayError::NotFound("unknown managed gateway agent".into()));
        }

        let row = sqlx::query(
            "SELECT payload_json, received_at
             FROM publications
             WHERE agent_id = ? AND message_type = 'agent.status.snapshot'
             ORDER BY received_at DESC
             LIMIT 1",
        )
        .bind(agent_id)
        .fetch_optional(&self.pool)
        .await?;

        row.map(|row| {
            let raw: String = row.try_get("payload_json")?;
            let message = serde_json::from_str::<AgentPublication>(&raw)?;
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
