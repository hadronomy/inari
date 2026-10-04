use std::collections::BTreeSet;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use url::Url;

use crate::error::ConfigError;

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct RouterPolicyConfig {
    pub fleet_id: String,
    pub routers: Vec<RouterManagementConfig>,
    pub trusted_peer_common_names: Vec<String>,
    pub signing_key_file: PathBuf,
    pub management_ca_file: PathBuf,
    pub management_certificate_file: PathBuf,
    pub management_private_key_file: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RouterManagementConfig {
    pub router_id: String,
    pub address: Url,
}

impl RouterPolicyConfig {
    #[cfg(test)]
    pub(crate) fn test_config() -> Self {
        Self {
            fleet_id: "test_fleet".into(),
            routers: vec![RouterManagementConfig {
                router_id: "router_test".into(),
                address: "https://router.example/"
                    .parse()
                    .unwrap(),
            }],
            trusted_peer_common_names: vec!["controller_test".into()],
            signing_key_file: "/test/policy-key".into(),
            management_ca_file: "/test/management-ca".into(),
            management_certificate_file: "/test/management-cert".into(),
            management_private_key_file: "/test/management-key".into(),
        }
    }

    pub(crate) fn validate(&self) -> Result<(), ConfigError> {
        let mut ids = BTreeSet::new();
        let mut addresses = BTreeSet::new();
        if self.routers.is_empty()
            || self.routers.len() > 32
            || self.fleet_id.is_empty()
            || self
                .trusted_peer_common_names
                .is_empty()
            || [
                &self.signing_key_file,
                &self.management_ca_file,
                &self.management_certificate_file,
                &self.management_private_key_file,
            ]
            .iter()
            .any(|path| path.as_os_str().is_empty())
        {
            return Err(ConfigError::invalid(
                "Router policy requires a fleet, every Router, trusted peers, a signing key, and management mTLS files.",
            ));
        }
        for router in &self.routers {
            let url = &router.address;
            if router.router_id.is_empty()
                || !ids.insert(&router.router_id)
                || !addresses.insert(url.as_str())
                || url.scheme() != "https"
                || url.host_str().is_none()
                || url.path() != "/"
                || !url.username().is_empty()
                || url.password().is_some()
                || url.query().is_some()
                || url.fragment().is_some()
            {
                return Err(ConfigError::invalid(
                    "Each Router requires a distinct ID and HTTPS management origin without credentials, query, or fragment.",
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn management_origins_are_explicit_https_authorities() {
        for address in [
            "http://router.example/",
            "https://router.example/policy",
            "https://user:secret@router.example/",
            "https://router.example/?query=1",
            "https://router.example/#fragment",
        ] {
            let mut config = RouterPolicyConfig::test_config();
            config.routers[0].address = address.parse().unwrap();
            assert!(config.validate().is_err(), "{address}");
        }
        let mut config = RouterPolicyConfig::test_config();
        assert!(config.validate().is_ok());
        config
            .routers
            .push(config.routers[0].clone());
        assert!(config.validate().is_err(), "a Router cannot be counted twice");
        config.routers.clear();
        assert!(config.validate().is_err(), "an empty fleet cannot acknowledge policy");
    }
}
