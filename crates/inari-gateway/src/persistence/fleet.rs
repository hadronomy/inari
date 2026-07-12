use chrono::{Duration, Utc};
use sqlx::Row;

use super::GatewayRepository;
use crate::protocol::{
    AgentHealth, AgentHealthState, AgentId, AgentSummary, DeviceSummary, OrganizationId, SiteId,
    SiteSummary,
};
use crate::{GatewayError, GatewayResult};

impl GatewayRepository {
    pub async fn sites(&self, organization_id: &OrganizationId) -> GatewayResult<Vec<SiteSummary>> {
        let rows = sqlx::query(
            "SELECT s.site_id, s.name,
                    COUNT(DISTINCT a.agent_id) AS agent_count,
                    COUNT(DISTINCT d.device_id) AS device_count
             FROM sites s
             LEFT JOIN agents a ON a.site_id = s.site_id
             LEFT JOIN devices d ON d.site_id = s.site_id
             WHERE s.organization_id = $1
             GROUP BY s.site_id, s.name
             ORDER BY s.name",
        )
        .bind(organization_id.as_str())
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                Ok(SiteSummary {
                    site_id: row
                        .try_get::<String, _>("site_id")?
                        .parse()?,
                    name: row.try_get("name")?,
                    agent_count: count(row, "agent_count")?,
                    device_count: count(row, "device_count")?,
                })
            })
            .collect()
    }

    pub async fn agents(
        &self,
        organization_id: &OrganizationId,
        site_id: Option<&SiteId>,
    ) -> GatewayResult<Vec<AgentSummary>> {
        let rows = sqlx::query(
            "SELECT a.agent_id, a.site_id, a.enrolled_at, MAX(p.received_at) AS last_seen_at
             FROM agents a
             LEFT JOIN publications p ON p.agent_id = a.agent_id
             WHERE a.organization_id = $1 AND ($2::TEXT IS NULL OR a.site_id = $2)
             GROUP BY a.agent_id, a.site_id, a.enrolled_at
             ORDER BY a.agent_id",
        )
        .bind(organization_id.as_str())
        .bind(site_id.map(SiteId::as_str))
        .fetch_all(&self.pool)
        .await?;
        let online_after = Utc::now() - Duration::minutes(2);
        rows.iter()
            .map(|row| {
                let last_seen_at = row.try_get("last_seen_at")?;
                let state = match last_seen_at {
                    Some(last_seen_at) if last_seen_at >= online_after => AgentHealthState::Online,
                    Some(_) => AgentHealthState::Offline,
                    None => AgentHealthState::AwaitingFirstContact,
                };
                Ok(AgentSummary {
                    agent_id: row
                        .try_get::<String, _>("agent_id")?
                        .parse()?,
                    site_id: row
                        .try_get::<String, _>("site_id")?
                        .parse()?,
                    health: AgentHealth { state, last_seen_at },
                    enrolled_at: row.try_get("enrolled_at")?,
                })
            })
            .collect()
    }

    pub async fn devices(&self, agent_id: &AgentId) -> GatewayResult<Vec<DeviceSummary>> {
        let rows = sqlx::query(
            "SELECT device_id, agent_id, site_id, kind, display_name, state, transport,
                    capabilities, last_seen_at
             FROM devices WHERE agent_id = $1 ORDER BY display_name",
        )
        .bind(agent_id.as_str())
        .fetch_all(&self.pool)
        .await?;
        rows.iter()
            .map(|row| {
                Ok(DeviceSummary {
                    device_id: row
                        .try_get::<String, _>("device_id")?
                        .parse()?,
                    agent_id: agent_id.clone(),
                    site_id: row
                        .try_get::<String, _>("site_id")?
                        .parse()?,
                    kind: parse_json_string(row, "kind")?,
                    display_name: row.try_get("display_name")?,
                    state: parse_json_string(row, "state")?,
                    transport: parse_json_string(row, "transport")?,
                    capabilities: serde_json::from_value(row.try_get("capabilities")?)?,
                    last_seen_at: row.try_get("last_seen_at")?,
                })
            })
            .collect()
    }
}

fn count(row: &sqlx::postgres::PgRow, column: &str) -> GatewayResult<u64> {
    u64::try_from(row.try_get::<i64, _>(column)?)
        .map_err(|_| GatewayError::CorruptState(format!("{column} is negative")))
}

fn parse_json_string<T>(row: &sqlx::postgres::PgRow, column: &str) -> GatewayResult<T>
where
    T: serde::de::DeserializeOwned,
{
    let value: String = row.try_get(column)?;
    serde_json::from_value(serde_json::Value::String(value)).map_err(Into::into)
}
