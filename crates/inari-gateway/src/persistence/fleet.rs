use chrono::{DateTime, Duration, FixedOffset, Utc};
use sea_orm::sea_query::Expr;
use sea_orm::{
    AccessMode, ColumnTrait, DatabaseBackend, EntityTrait, ExprTrait, FromQueryResult,
    IsolationLevel, QueryFilter, QueryOrder, QuerySelect, QueryTrait, Statement, TransactionTrait,
};

use super::entity::value::{
    DeviceClass as StoredDeviceClass, DeviceKind as StoredDeviceKind,
    DeviceState as StoredDeviceState, DeviceTransport as StoredDeviceTransport,
};
use super::entity::{agent, device, organization, publication, site};
use super::{GatewayRepository, utc_time};
use crate::protocol::{
    AgentHealth, AgentHealthState, AgentId, AgentSummary, DeviceClass, DeviceKind, DeviceState,
    DeviceSummary, DeviceTransport, OrganizationId, OrganizationWorkloadScope, SiteId, SiteSummary,
    WorkloadInventory,
};
use crate::{GatewayError, GatewayResult};

#[derive(Debug, FromQueryResult)]
struct SiteCounts {
    site_id: String,
    name: String,
    agent_count: i64,
    device_count: i64,
}

#[derive(Debug, FromQueryResult)]
struct AgentRow {
    agent_id: String,
    site_id: String,
    enrolled_at: DateTime<FixedOffset>,
    last_seen_at: Option<DateTime<FixedOffset>>,
}

#[derive(Debug, FromQueryResult)]
struct InventoryObservation {
    observed_at: DateTime<FixedOffset>,
}

impl GatewayRepository {
    pub async fn workload_inventory(
        &self,
        scope: &OrganizationWorkloadScope,
    ) -> GatewayResult<WorkloadInventory> {
        const LIMIT: u64 = 2_000;
        let transaction = self
            .database
            .begin_with_config(Some(IsolationLevel::RepeatableRead), Some(AccessMode::ReadOnly))
            .await?;
        let observation = InventoryObservation::find_by_statement(Statement::from_string(
            DatabaseBackend::Postgres,
            "SELECT transaction_timestamp() AS observed_at",
        ))
        .one(&transaction)
        .await?
        .ok_or_else(|| GatewayError::NotFound("Inventory observation time is absent.".into()))?;
        let organization = organization::Entity::find_by_id(scope.organization_id.as_str())
            .one(&transaction)
            .await?
            .ok_or_else(|| GatewayError::NotFound("Organization was not found.".into()))?;
        let sites = site::Entity::find()
            .filter(
                site::COLUMN
                    .organization_id
                    .eq(scope.organization_id.as_str()),
            )
            .order_by_asc(site::COLUMN.site_id)
            .limit(LIMIT + 1)
            .all(&transaction)
            .await?;
        let agents = agent::Entity::find()
            .select_only()
            .column(agent::COLUMN.agent_id.0)
            .column(agent::COLUMN.site_id.0)
            .column(agent::COLUMN.enrolled_at.0)
            .column_as(publication::COLUMN.received_at.0.max(), "last_seen_at")
            .left_join(publication::Entity)
            .filter(
                agent::COLUMN
                    .organization_id
                    .eq(scope.organization_id.as_str()),
            )
            .group_by(agent::COLUMN.agent_id)
            .group_by(agent::COLUMN.site_id)
            .group_by(agent::COLUMN.enrolled_at)
            .order_by_asc(agent::COLUMN.agent_id)
            .limit(LIMIT + 1)
            .into_model::<AgentRow>()
            .all(&transaction)
            .await?;
        let devices = device::Entity::find()
            .filter(
                device::COLUMN.agent_id.in_subquery(
                    agent::Entity::find()
                        .select_only()
                        .column(agent::COLUMN.agent_id.0)
                        .filter(
                            agent::COLUMN
                                .organization_id
                                .eq(scope.organization_id.as_str()),
                        )
                        .into_query(),
                ),
            )
            .order_by_asc(device::COLUMN.agent_id)
            .order_by_asc(device::COLUMN.device_id)
            .limit(LIMIT + 1)
            .all(&transaction)
            .await?;
        if [sites.len(), agents.len(), devices.len()]
            .into_iter()
            .any(|count| count > LIMIT as usize)
        {
            return Err(GatewayError::Capacity(
                "The Organization inventory exceeds the full synchronization limit.".into(),
            ));
        }
        let agents: Vec<_> = agents
            .into_iter()
            .map(agent_summary)
            .collect::<GatewayResult<_>>()?;
        let devices: Vec<_> = devices
            .into_iter()
            .map(device_summary)
            .collect::<GatewayResult<_>>()?;
        let sites = sites
            .into_iter()
            .map(|site| {
                Ok(SiteSummary {
                    site_id: site.site_id.parse()?,
                    name: site.name,
                    agent_count: agents
                        .iter()
                        .filter(|agent| agent.site_id.as_str() == site.site_id)
                        .count() as u64,
                    device_count: devices
                        .iter()
                        .filter(|device| device.site_id.as_str() == site.site_id)
                        .count() as u64,
                })
            })
            .collect::<GatewayResult<_>>()?;
        transaction.commit().await?;
        Ok(WorkloadInventory {
            scope: scope.clone(),
            organization_name: organization.name,
            observed_at: observation
                .observed_at
                .with_timezone(&Utc),
            sites,
            agents,
            devices,
        })
    }

    pub async fn sites(&self, organization_id: &OrganizationId) -> GatewayResult<Vec<SiteSummary>> {
        site::Entity::find()
            .select_only()
            .column(site::COLUMN.site_id.0)
            .column(site::COLUMN.name.0)
            .column_as(
                Expr::col(agent::COLUMN.agent_id.as_column_ref()).count_distinct(),
                "agent_count",
            )
            .column_as(
                Expr::cust("COUNT(DISTINCT (devices.agent_id, devices.device_id)) FILTER (WHERE devices.device_id IS NOT NULL)"),
                "device_count",
            )
            .left_join(agent::Entity)
            .left_join(device::Entity)
            .filter(
                site::COLUMN
                    .organization_id
                    .eq(organization_id.as_str()),
            )
            .group_by(site::COLUMN.site_id)
            .group_by(site::COLUMN.name)
            .order_by_asc(site::COLUMN.name)
            .into_model::<SiteCounts>()
            .all(&self.database)
            .await?
            .into_iter()
            .map(|row| {
                Ok(SiteSummary {
                    site_id: row.site_id.parse()?,
                    name: row.name,
                    agent_count: count(row.agent_count, "agent_count")?,
                    device_count: count(row.device_count, "device_count")?,
                })
            })
            .collect()
    }

    pub async fn agents(
        &self,
        organization_id: &OrganizationId,
        site_id: Option<&SiteId>,
    ) -> GatewayResult<Vec<AgentSummary>> {
        let mut query = agent::Entity::find()
            .select_only()
            .column(agent::COLUMN.agent_id.0)
            .column(agent::COLUMN.site_id.0)
            .column(agent::COLUMN.enrolled_at.0)
            .column_as(publication::COLUMN.received_at.0.max(), "last_seen_at")
            .left_join(publication::Entity)
            .filter(
                agent::COLUMN
                    .organization_id
                    .eq(organization_id.as_str()),
            )
            .group_by(agent::COLUMN.agent_id)
            .group_by(agent::COLUMN.site_id)
            .group_by(agent::COLUMN.enrolled_at)
            .order_by_asc(agent::COLUMN.agent_id);
        if let Some(site_id) = site_id {
            query = query.filter(
                agent::COLUMN
                    .site_id
                    .eq(site_id.as_str()),
            );
        }
        query
            .into_model::<AgentRow>()
            .all(&self.database)
            .await?
            .into_iter()
            .map(agent_summary)
            .collect()
    }

    pub async fn devices(&self, agent_id: &AgentId) -> GatewayResult<Vec<DeviceSummary>> {
        device::Entity::find()
            .filter(
                device::COLUMN
                    .agent_id
                    .eq(agent_id.as_str()),
            )
            .order_by_asc(device::COLUMN.display_name)
            .all(&self.database)
            .await?
            .into_iter()
            .map(device_summary)
            .collect()
    }
}

fn device_summary(model: device::Model) -> GatewayResult<DeviceSummary> {
    Ok(DeviceSummary {
        device_id: model.device_id.parse()?,
        agent_id: model.agent_id.parse()?,
        site_id: model.site_id.parse()?,
        kind: model.kind.into(),
        device_class: match model.device_class {
            StoredDeviceClass::Physical => DeviceClass::Physical,
            StoredDeviceClass::Virtual => DeviceClass::Virtual,
        },
        display_name: model.display_name,
        state: model.state.into(),
        transport: model.transport.into(),
        capabilities: model.capabilities.0,
        last_seen_at: utc_time(model.last_seen_at),
    })
}

fn agent_summary(row: AgentRow) -> GatewayResult<AgentSummary> {
    let last_seen_at = row.last_seen_at.map(utc_time);
    let online_after = Utc::now() - Duration::minutes(2);
    let state = match last_seen_at {
        Some(last_seen_at) if last_seen_at >= online_after => AgentHealthState::Online,
        Some(_) => AgentHealthState::Offline,
        None => AgentHealthState::AwaitingFirstContact,
    };
    Ok(AgentSummary {
        agent_id: row.agent_id.parse()?,
        site_id: row.site_id.parse()?,
        health: AgentHealth { state, last_seen_at },
        enrolled_at: utc_time(row.enrolled_at),
    })
}

fn count(value: i64, name: &str) -> GatewayResult<u64> {
    u64::try_from(value).map_err(|_| GatewayError::CorruptState(format!("{name} is negative")))
}

impl From<StoredDeviceKind> for DeviceKind {
    fn from(value: StoredDeviceKind) -> Self {
        match value {
            StoredDeviceKind::Printer => Self::Printer,
            StoredDeviceKind::Scale => Self::Scale,
            StoredDeviceKind::Scanner => Self::Scanner,
            StoredDeviceKind::Display => Self::Display,
        }
    }
}

impl From<StoredDeviceState> for DeviceState {
    fn from(value: StoredDeviceState) -> Self {
        match value {
            StoredDeviceState::Discovered => Self::Discovered,
            StoredDeviceState::PendingApproval => Self::PendingApproval,
            StoredDeviceState::Online => Self::Online,
            StoredDeviceState::Offline => Self::Offline,
            StoredDeviceState::Degraded => Self::Degraded,
            StoredDeviceState::Blocked => Self::Blocked,
        }
    }
}

impl From<StoredDeviceTransport> for DeviceTransport {
    fn from(value: StoredDeviceTransport) -> Self {
        match value {
            StoredDeviceTransport::Spooler => Self::Spooler,
            StoredDeviceTransport::Network => Self::Network,
            StoredDeviceTransport::Usb => Self::Usb,
            StoredDeviceTransport::Hid => Self::Hid,
            StoredDeviceTransport::Serial => Self::Serial,
        }
    }
}
