use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{AgentId, DeviceId, GatewaySnapshot, SiteId};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentStatus {
    pub agent_id: AgentId,
    pub message_id: String,
    pub received_at: DateTime<Utc>,
    pub snapshot: GatewaySnapshot,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SiteSummary {
    pub site_id: SiteId,
    pub name: String,
    pub agent_count: u64,
    pub device_count: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentSummary {
    pub agent_id: AgentId,
    pub site_id: SiteId,
    pub health: AgentHealth,
    pub enrolled_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentDetail {
    pub summary: AgentSummary,
    pub latest_status: Option<AgentStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentHealth {
    pub state: AgentHealthState,
    pub last_seen_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentHealthState {
    Online,
    Offline,
    AwaitingFirstContact,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceSummary {
    pub device_id: DeviceId,
    pub agent_id: AgentId,
    pub site_id: SiteId,
    pub kind: DeviceKind,
    pub display_name: String,
    pub state: DeviceState,
    pub transport: DeviceTransport,
    pub capabilities: Vec<DeviceCapability>,
    pub last_seen_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceKind {
    Printer,
    Scale,
    Scanner,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceState {
    Discovered,
    PendingApproval,
    Online,
    Offline,
    Degraded,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceTransport {
    Spooler,
    Network,
    Usb,
    Hid,
    Serial,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceCapability {
    Print,
    Weigh,
    Scan,
    CashDrawer,
    Cut,
    Status,
}
