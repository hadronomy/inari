use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use super::{ProtocolDescriptor, StructuredFields};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewaySnapshot {
    pub generated_at: DateTime<Utc>,
    pub protocol: ProtocolDescriptor,
    pub service: ServiceDescriptor,
    pub security: SecurityDescriptor,
    pub runtime: RuntimeDescriptor,
    pub capabilities: CapabilityDescriptor,
    #[serde(default)]
    pub observability: StructuredFields,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ServiceDescriptor {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default, flatten)]
    pub attributes: StructuredFields,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SecurityDescriptor {
    pub mode: String,
    pub exposure: String,
    pub tls_required: bool,
    pub certificate_mode: String,
    pub mutual_tls_mode: String,
    pub mutual_tls_enabled: bool,
    #[serde(default)]
    pub certificate_expires_at: Option<DateTime<Utc>>,
    #[serde(default, flatten)]
    pub attributes: StructuredFields,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RuntimeDescriptor {
    #[serde(default)]
    pub queue: std::collections::BTreeMap<String, u64>,
    #[serde(default)]
    pub devices: StructuredFields,
    #[serde(default)]
    pub inventory: StructuredFields,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CapabilityDescriptor {
    #[serde(default)]
    pub supported_content_kinds: Vec<String>,
    #[serde(default)]
    pub supported_device_commands: Vec<String>,
    #[serde(default)]
    pub supported_controller_actions: Vec<String>,
    #[serde(default)]
    pub features: Vec<String>,
    pub transport: String,
    #[serde(default)]
    pub client_certificate_present: bool,
}
