use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use inari_gateway::protocol::{OrganizationId, SiteId};

use crate::error::ConfigError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct DatabaseConfig {
    pub url_file: PathBuf,
    pub migrate_on_startup: bool,
    pub min_connections: u32,
    pub max_connections: u32,
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            url_file: PathBuf::from("/var/run/secrets/inari/database-url"),
            migrate_on_startup: true,
            min_connections: 1,
            max_connections: 16,
        }
    }
}

impl DatabaseConfig {
    pub(super) fn validate(&self, required: bool) -> Result<(), ConfigError> {
        if required && self.url_file.as_os_str().is_empty() {
            return Err(ConfigError::invalid(
                "database.url_file is required when managed controller features are enabled.",
            ));
        }
        if self.max_connections == 0 || self.min_connections > self.max_connections {
            return Err(ConfigError::invalid(
                "database connection limits must be non-zero and min_connections must not exceed max_connections.",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OrganizationConfig {
    pub id: OrganizationId,
    pub name: String,
    pub default_site_id: SiteId,
    pub default_site_name: String,
}

impl Default for OrganizationConfig {
    fn default() -> Self {
        Self {
            id: "org_default"
                .parse()
                .expect("default organization ID must be valid"),
            name: "Inari organization".into(),
            default_site_id: "site_default"
                .parse()
                .expect("default site ID must be valid"),
            default_site_name: "Default site".into(),
        }
    }
}

impl OrganizationConfig {
    pub(super) fn validate(&self) -> Result<(), ConfigError> {
        if self.name.trim().is_empty() || self.default_site_name.trim().is_empty() {
            return Err(ConfigError::invalid(
                "organization identity and default site fields must not be empty.",
            ));
        }
        Ok(())
    }
}
