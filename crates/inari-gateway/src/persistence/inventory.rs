use std::collections::BTreeSet;

use chrono::{DateTime, Utc};
use sea_orm::sea_query::OnConflict;
use sea_orm::{ActiveValue::Set, ColumnTrait, DatabaseTransaction, EntityTrait, QueryFilter};
use serde::Deserialize;

use super::entity::device;
use super::entity::value::{
    DeviceClass as StoredDeviceClass, DeviceKind as StoredDeviceKind,
    DeviceState as StoredDeviceState, DeviceTransport as StoredDeviceTransport, StoredCapabilities,
};
use super::stored_time;
use crate::protocol::{DeviceClass, DeviceId, DeviceKind, DeviceTransport, StructuredFields};
use crate::{GatewayError, GatewayResult};

const MAX_DEVICES: usize = 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct InventoryItem {
    device_id: DeviceId,
    kind: DeviceKind,
    device_class: DeviceClass,
    display_name: String,
    system_name: String,
    driver_key: String,
    connection_state: ConnectionState,
    transport: DeviceTransport,
    identity_digest: String,
    capabilities: Vec<String>,
    metadata: StructuredFields,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ConnectionState {
    Online,
    Offline,
}

/// A complete, bounded inventory. Construction rejects the whole snapshot on ambiguity.
#[derive(Debug)]
pub(super) struct InventoryProjection {
    devices: Vec<InventoryItem>,
}

impl InventoryProjection {
    pub(super) fn parse(inventory: &StructuredFields) -> GatewayResult<Self> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Inventory {
            devices: Vec<InventoryItem>,
        }

        let inventory: Inventory = serde_json::from_value(serde_json::to_value(inventory)?)
            .map_err(|_| invalid("inventory does not match the Device contract"))?;
        if inventory.devices.len() > MAX_DEVICES {
            return Err(invalid("inventory exceeds the Device count limit"));
        }
        let mut device_ids = BTreeSet::new();
        let mut identities = BTreeSet::new();
        for item in &inventory.devices {
            if !device_ids.insert(item.device_id.clone())
                || !identities.insert((kind_name(item.kind), item.identity_digest.clone()))
            {
                return Err(invalid("inventory contains duplicate Device identities"));
            }
            for value in [&item.display_name, &item.system_name, &item.driver_key] {
                if value.is_empty() || value.len() > 1024 || value.chars().any(char::is_control) {
                    return Err(invalid("inventory contains an invalid Device name"));
                }
            }
            if item.identity_digest.len() != 64
                || !item
                    .identity_digest
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
            {
                return Err(invalid("inventory contains an invalid identity digest"));
            }
            let mut capabilities = BTreeSet::new();
            if item.capabilities.iter().any(|value| {
                !matches!(value.as_str(), "raw" | "text" | "documents" | "cash_drawer")
                    || !capabilities.insert(value)
            }) {
                return Err(invalid("inventory contains an invalid discovery capability"));
            }
            if serde_json::to_vec(&item.metadata)?.len() > 16384 {
                return Err(invalid("inventory Device metadata exceeds its size limit"));
            }
        }
        Ok(Self { devices: inventory.devices })
    }

    /// Replaces this Agent's Projection while retaining the first observation of each Device.
    /// The caller holds the enrollment invitation and Agent locks through commit.
    pub(super) async fn apply(
        &self,
        transaction: &DatabaseTransaction,
        agent_id: &str,
        site_id: &str,
        generated_at: DateTime<Utc>,
    ) -> GatewayResult<()> {
        let existing = device::Entity::find()
            .filter(device::Column::AgentId.eq(agent_id))
            .all(transaction)
            .await?;
        for item in &self.devices {
            if existing.iter().any(|stored| {
                let same_identity = stored.identity_digest == item.identity_digest
                    && kind_name(stored.kind.clone().into()) == kind_name(item.kind);
                (stored.device_id == item.device_id.as_str()) != same_identity
            }) {
                return Err(invalid("a Device changed its established identity"));
            }
            device::Entity::insert(device::ActiveModel {
                agent_id: Set(agent_id.to_owned()),
                device_id: Set(item.device_id.as_str().to_owned()),
                site_id: Set(site_id.to_owned()),
                kind: Set(match item.kind {
                    DeviceKind::Printer => StoredDeviceKind::Printer,
                    DeviceKind::Scale => StoredDeviceKind::Scale,
                    DeviceKind::Scanner => StoredDeviceKind::Scanner,
                    DeviceKind::Display => StoredDeviceKind::Display,
                }),
                device_class: Set(match item.device_class {
                    DeviceClass::Physical => StoredDeviceClass::Physical,
                    DeviceClass::Virtual => StoredDeviceClass::Virtual,
                }),
                display_name: Set(item.display_name.clone()),
                state: Set(match item.connection_state {
                    ConnectionState::Online => StoredDeviceState::Online,
                    ConnectionState::Offline => StoredDeviceState::Offline,
                }),
                transport: Set(match item.transport {
                    DeviceTransport::Spooler => StoredDeviceTransport::Spooler,
                    DeviceTransport::Network => StoredDeviceTransport::Network,
                    DeviceTransport::Usb => StoredDeviceTransport::Usb,
                    DeviceTransport::Hid => StoredDeviceTransport::Hid,
                    DeviceTransport::Serial => StoredDeviceTransport::Serial,
                }),
                identity_digest: Set(item.identity_digest.clone()),
                capabilities: Set(StoredCapabilities(vec![])),
                first_seen_at: Set(stored_time(generated_at)),
                last_seen_at: Set(stored_time(generated_at)),
            })
            .on_conflict(
                OnConflict::columns([device::Column::AgentId, device::Column::DeviceId])
                    .update_columns([
                        device::Column::SiteId,
                        device::Column::DeviceClass,
                        device::Column::DisplayName,
                        device::Column::State,
                        device::Column::Transport,
                        device::Column::Capabilities,
                        device::Column::LastSeenAt,
                    ])
                    .to_owned(),
            )
            .exec(transaction)
            .await?;
        }
        let mut withdrawn =
            device::Entity::delete_many().filter(device::Column::AgentId.eq(agent_id));
        if !self.devices.is_empty() {
            withdrawn = withdrawn.filter(
                device::Column::DeviceId.is_not_in(
                    self.devices
                        .iter()
                        .map(|item| item.device_id.as_str()),
                ),
            );
        }
        withdrawn.exec(transaction).await?;
        Ok(())
    }
}

fn kind_name(kind: DeviceKind) -> &'static str {
    match kind {
        DeviceKind::Printer => "printer",
        DeviceKind::Scale => "scale",
        DeviceKind::Scanner => "scanner",
        DeviceKind::Display => "display",
    }
}

fn invalid(detail: &str) -> GatewayError {
    GatewayError::InvalidInput(detail.to_owned())
}
