use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use url::Url;

use crate::config::ServerConfig;
use crate::error::ConfigError;
use crate::identity::AccessRole;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct IdentityConfig {
    pub oidc: OidcConfig,
}

impl IdentityConfig {
    pub(super) fn validate(&self, server: &ServerConfig) -> Result<(), ConfigError> {
        self.oidc.validate(server)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OidcConfig {
    pub enabled: bool,
    pub issuer_url: Option<Url>,
    pub client_id: String,
    pub client_secret_file: Option<PathBuf>,
    pub scopes: Vec<String>,
    pub role_claim: String,
    pub role_mapping: BTreeMap<String, AccessRole>,
}

impl Default for OidcConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            issuer_url: None,
            client_id: String::new(),
            client_secret_file: None,
            scopes: vec!["openid".into(), "profile".into(), "email".into()],
            role_claim: "roles".into(),
            role_mapping: BTreeMap::new(),
        }
    }
}

impl OidcConfig {
    fn validate(&self, server: &ServerConfig) -> Result<(), ConfigError> {
        if !self.enabled {
            return Ok(());
        }
        let issuer = self
            .issuer_url
            .as_ref()
            .ok_or_else(|| {
                ConfigError::invalid("identity.oidc.issuer_url is required when OIDC is enabled.")
            })?;
        if issuer.scheme() != "https" && server.environment.is_deployed() {
            return Err(ConfigError::invalid(
                "identity.oidc.issuer_url must use HTTPS outside development.",
            ));
        }
        if self.client_id.trim().is_empty() {
            return Err(ConfigError::invalid(
                "identity.oidc.client_id is required when OIDC is enabled.",
            ));
        }
        if self
            .scopes
            .iter()
            .all(|scope| scope != "openid")
        {
            return Err(ConfigError::invalid("identity.oidc.scopes must include `openid`."));
        }
        if self.role_claim.trim().is_empty() || self.role_mapping.is_empty() {
            return Err(ConfigError::invalid(
                "identity.oidc.role_claim and role_mapping are required when OIDC is enabled.",
            ));
        }
        let public_url = server
            .public_url
            .as_ref()
            .ok_or_else(|| {
                ConfigError::invalid("server.public_url is required when OIDC is enabled.")
            })?;
        if server.environment.is_deployed() && public_url.scheme() != "https" {
            return Err(ConfigError::invalid(
                "server.public_url must use HTTPS outside development.",
            ));
        }
        Ok(())
    }
}
