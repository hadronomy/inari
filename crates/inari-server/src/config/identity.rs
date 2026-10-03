use std::collections::BTreeMap;
use std::path::PathBuf;

use openidconnect::IssuerUrl;
use serde::{Deserialize, Serialize};

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
    /// OIDC compares issuer identifiers exactly, including a root trailing slash.
    pub issuer_url: Option<IssuerUrl>,
    pub client_id: String,
    /// Additional trusted ID token audiences; the client ID is always required.
    pub additional_id_token_audiences: Vec<String>,
    pub client_secret_file: Option<PathBuf>,
    pub workload_audience: String,
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
            additional_id_token_audiences: Vec::new(),
            client_secret_file: None,
            workload_audience: "urn:inari:managed-workload".into(),
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
        if issuer.url().scheme() != "https" && server.environment.is_deployed() {
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
            .additional_id_token_audiences
            .iter()
            .any(|audience| audience.is_empty() || audience.trim() != audience)
        {
            return Err(ConfigError::invalid(
                "identity.oidc.additional_id_token_audiences must contain non-empty identifiers without surrounding whitespace.",
            ));
        }
        if self.workload_audience.trim().is_empty() {
            return Err(ConfigError::invalid("identity.oidc.workload_audience must not be empty."));
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn additional_id_token_audiences_require_exact_identifiers() {
        let server = ServerConfig {
            public_url: Some(
                "https://controller.example.com"
                    .parse()
                    .unwrap(),
            ),
            ..ServerConfig::default()
        };
        let mut config = OidcConfig {
            enabled: true,
            issuer_url: Some(IssuerUrl::new("https://identity.example.com".into()).unwrap()),
            client_id: "controller".into(),
            role_mapping: BTreeMap::from([("admin".into(), AccessRole::Administrator)]),
            ..OidcConfig::default()
        };
        assert!(
            config
                .additional_id_token_audiences
                .is_empty()
        );
        assert!(config.validate(&server).is_ok());
        for audience in ["", " ", " project", "project "] {
            config.additional_id_token_audiences = vec![audience.into()];
            assert!(config.validate(&server).is_err());
        }
        config.additional_id_token_audiences = vec!["project".into()];
        assert!(config.validate(&server).is_ok());
    }

    #[test]
    fn preserves_the_exact_oidc_issuer_for_discovery() {
        for issuer in [
            "https://auth.example.com",
            "https://auth.example.com/",
            "https://auth.example.com/realms/store",
            "https://auth.example.com/realms/store/",
        ] {
            let config: OidcConfig = toml::from_str(&format!("issuer_url = {issuer:?}")).unwrap();
            assert_eq!(
                config
                    .issuer_url
                    .as_ref()
                    .unwrap()
                    .as_str(),
                issuer
            );
            let encoded = toml::to_string(&config).unwrap();
            let decoded: OidcConfig = toml::from_str(&encoded).unwrap();
            assert_eq!(decoded.issuer_url.unwrap().as_str(), issuer);
        }
    }
}
