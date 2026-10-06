use std::path::PathBuf;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use url::Url;

use crate::error::ConfigError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct OpenBaoConfig {
    pub address: Option<Url>,
    pub kubernetes_role: Option<String>,
    pub kubernetes_auth_mount: String,
    pub service_account_token_file: PathBuf,
    pub namespace: Option<String>,
    pub ca_certificate_file: Option<PathBuf>,
    #[serde(with = "humantime_serde")]
    pub request_timeout: Duration,
}

impl Default for OpenBaoConfig {
    fn default() -> Self {
        Self {
            address: None,
            kubernetes_role: None,
            kubernetes_auth_mount: "kubernetes".into(),
            service_account_token_file: PathBuf::from(
                "/var/run/secrets/kubernetes.io/serviceaccount/token",
            ),
            namespace: None,
            ca_certificate_file: None,
            request_timeout: Duration::from_secs(5),
        }
    }
}

impl OpenBaoConfig {
    pub(crate) fn validate(&self, required: bool) -> Result<(), ConfigError> {
        if !required && self.address.is_none() {
            return Ok(());
        }
        if self
            .address
            .as_ref()
            .is_none_or(|address| {
                address.scheme() != "https"
                    || address.host_str().is_none()
                    || address.path() != "/"
                    || address.query().is_some()
                    || address.fragment().is_some()
                    || !address.username().is_empty()
                    || address.password().is_some()
            })
        {
            return Err(ConfigError::invalid("openbao.address must be an HTTPS origin."));
        }
        if self
            .kubernetes_role
            .as_deref()
            .is_none_or(|role| !valid_openbao_name(role))
            || !valid_openbao_name(&self.kubernetes_auth_mount)
        {
            return Err(ConfigError::invalid(
                "openbao role and authentication mount need 1 to 128 ASCII letters, digits, hyphens, or underscores.",
            ));
        }
        if self
            .service_account_token_file
            .as_os_str()
            .is_empty()
            || self.request_timeout.is_zero()
        {
            return Err(ConfigError::invalid(
                "openbao requires an identity token file and a non-zero request timeout.",
            ));
        }
        Ok(())
    }
}

pub(crate) fn valid_openbao_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

#[cfg(test)]
mod tests {
    use super::OpenBaoConfig;

    #[test]
    fn required_connection_rejects_missing_or_credential_bearing_origins() {
        assert!(
            OpenBaoConfig::default()
                .validate(false)
                .is_ok()
        );
        assert!(
            OpenBaoConfig::default()
                .validate(true)
                .is_err()
        );
        for address in [
            "http://openbao.example/",
            "https://user:password@openbao.example/",
            "https://openbao.example/v1/",
            "https://openbao.example/?token=value",
            "https://openbao.example/#fragment",
        ] {
            let config = OpenBaoConfig {
                address: Some(address.parse().unwrap()),
                kubernetes_role: Some("controller".into()),
                ..Default::default()
            };
            assert!(config.validate(true).is_err());
        }
        let config = OpenBaoConfig {
            address: Some(
                "https://openbao.example/"
                    .parse()
                    .unwrap(),
            ),
            kubernetes_role: Some("controller".into()),
            ..Default::default()
        };
        assert!(config.validate(true).is_ok());
    }
}
