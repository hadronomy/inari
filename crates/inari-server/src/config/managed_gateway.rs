use std::path::PathBuf;
use std::time::Duration;

use inari_gateway::protocol::ProtocolVersion;
use serde::{Deserialize, Serialize};
use url::Url;
use zenoh::key_expr::OwnedKeyExpr;

use super::{RouterPolicyConfig, valid_openbao_name};
use crate::error::ConfigError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayConfig {
    pub enabled: bool,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub supported_protocol_versions: Vec<ProtocolVersion>,
    pub controller_actions: Vec<String>,
    pub onboarding: ManagedGatewayOnboardingConfig,
    pub data_plane: ManagedGatewayDataPlaneConfig,
    pub router_policy: RouterPolicyConfig,
    pub certificate: ManagedGatewayCertificateConfig,
    pub dispatch: ManagedGatewayDispatchConfig,
    pub payload_protection: ManagedGatewayPayloadProtectionConfig,
}

impl Default for ManagedGatewayConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            controller_name: Some("Inari Controller".into()),
            controller_instance_id: "inari-server".into(),
            supported_protocol_versions: vec![ProtocolVersion::current()],
            controller_actions: [
                "system:read",
                "devices:read",
                "events:read",
                "jobs:cancel",
                "commands:execute",
                "managed_work:dispatch",
            ]
            .map(String::from)
            .to_vec(),
            onboarding: ManagedGatewayOnboardingConfig::default(),
            data_plane: ManagedGatewayDataPlaneConfig::default(),
            router_policy: RouterPolicyConfig::default(),
            certificate: ManagedGatewayCertificateConfig::default(),
            dispatch: ManagedGatewayDispatchConfig::default(),
            payload_protection: ManagedGatewayPayloadProtectionConfig::default(),
        }
    }
}

impl ManagedGatewayConfig {
    pub(super) fn validate(&self) -> Result<(), ConfigError> {
        if !self.enabled {
            return Ok(());
        }
        self.router_policy.validate()?;
        if self
            .supported_protocol_versions
            .is_empty()
        {
            return Err(ConfigError::invalid(
                "managed_gateway.supported_protocol_versions must not be empty.",
            ));
        }
        if self
            .data_plane
            .connect_endpoints
            .is_empty()
        {
            return Err(ConfigError::invalid(
                "managed_gateway.data_plane.connect_endpoints must not be empty.",
            ));
        }
        OwnedKeyExpr::try_from(
            self.data_plane
                .namespace_prefix
                .as_str(),
        )
        .map_err(|source| {
            ConfigError::invalid(format!(
                "managed_gateway.data_plane.namespace_prefix is invalid: {source}"
            ))
        })?;
        if self.onboarding.enabled {
            let public_base_url = self
                .onboarding
                .public_base_url
                .as_deref()
                .ok_or_else(|| {
                    ConfigError::invalid(
                        "managed_gateway.onboarding.public_base_url is required when onboarding is enabled.",
                    )
                })?
                .parse::<Url>()
                .map_err(|source| {
                    ConfigError::invalid(
                        "managed_gateway.onboarding.public_base_url is invalid.",
                    )
                    .with_source(source)
                })?;
            if !matches!(public_base_url.scheme(), "http" | "https") {
                return Err(ConfigError::invalid(
                    "managed_gateway.onboarding.public_base_url must use HTTP or HTTPS.",
                ));
            }
            if self.onboarding.invite_ttl.is_zero()
                || self
                    .onboarding
                    .failed_attempt_window
                    .is_zero()
                || self.onboarding.max_failed_attempts == 0
            {
                return Err(ConfigError::invalid(
                    "Managed onboarding durations and max_failed_attempts must be non-zero.",
                ));
            }
        }
        if self.certificate.mode == ManagedGatewayCertificateMode::StepCa {
            if self
                .certificate
                .step_ca_base_url
                .as_ref()
                .is_none_or(|url| {
                    url.scheme() != "https"
                        || url.host_str().is_none()
                        || !url.username().is_empty()
                        || url.password().is_some()
                        || url.query().is_some()
                        || url.fragment().is_some()
                })
                || self
                    .certificate
                    .step_ca_root_fingerprint
                    .as_deref()
                    .is_none_or(|fingerprint| {
                        fingerprint.len() != 64
                            || !fingerprint
                                .bytes()
                                .all(|byte| byte.is_ascii_hexdigit())
                    })
                || self
                    .certificate
                    .step_ca_provisioner
                    .as_deref()
                    .is_none_or(str::is_empty)
                || self
                    .certificate
                    .step_ca_key_id
                    .as_deref()
                    .is_none_or(str::is_empty)
                || self
                    .certificate
                    .step_ca_signing_key_file
                    .is_none()
            {
                return Err(ConfigError::invalid(
                    "step-ca mode requires an HTTPS base_url without credentials, query, or fragment, a SHA-256 root_fingerprint, provisioner, key_id, and signing_key_file.",
                ));
            }
            if !(Duration::from_secs(60)..=Duration::from_secs(60 * 60))
                .contains(&self.certificate.step_ca_token_ttl)
            {
                return Err(ConfigError::invalid(
                    "managed_gateway.certificate.step_ca_token_ttl must be between 1 minute and 1 hour.",
                ));
            }
        }
        if self.dispatch.enabled {
            if self
                .dispatch
                .signing_key_id
                .as_deref()
                .is_none_or(str::is_empty)
                || self.dispatch.signing_key_file.is_none()
                || self.dispatch.epoch == 0
            {
                return Err(ConfigError::invalid(
                    "managed_gateway.dispatch requires a signing_key_id, signing_key_file, and non-zero epoch.",
                ));
            }
            if !self
                .controller_actions
                .iter()
                .any(|action| action == "managed_work:dispatch")
            {
                return Err(ConfigError::invalid(
                    "managed_gateway.dispatch requires the managed_work:dispatch Controller action.",
                ));
            }
            if !(Duration::from_secs(10)..=Duration::from_secs(5 * 60))
                .contains(&self.dispatch.envelope_ttl)
            {
                return Err(ConfigError::invalid(
                    "managed_gateway.dispatch.envelope_ttl must be between 10 seconds and 5 minutes.",
                ));
            }
            if !self.payload_protection.enabled {
                return Err(ConfigError::invalid(
                    "managed_gateway.dispatch requires managed_gateway.payload_protection.",
                ));
            }
        }
        if self.payload_protection.enabled {
            for value in
                [&self.payload_protection.transit_mount, &self.payload_protection.transit_key_name]
            {
                if !valid_openbao_name(value) {
                    return Err(ConfigError::invalid(
                        "managed_gateway.payload_protection Transit names need 1 to 128 ASCII letters, digits, hyphens, or underscores.",
                    ));
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod payload_tests {
    use super::{ManagedGatewayConfig, ManagedGatewayPayloadProtectionConfig};

    fn config() -> ManagedGatewayConfig {
        let mut config = ManagedGatewayConfig {
            enabled: true,
            router_policy: super::super::RouterPolicyConfig::test_config(),
            payload_protection: ManagedGatewayPayloadProtectionConfig {
                enabled: true,
                ..Default::default()
            },
            ..Default::default()
        };
        config.data_plane.connect_endpoints = vec!["tls/router.example:7447".into()];
        config
    }

    #[test]
    fn payload_protection_rejects_path_components_in_mount_names() {
        for name in ["", "../transit", "transit/key", "%2f", "transit\n"] {
            let mut config = config();
            config.payload_protection.transit_mount = name.into();
            assert!(config.validate().is_err());
        }
    }

    #[test]
    fn managed_dispatch_requires_payload_protection() {
        let mut config = config();
        config.dispatch.enabled = true;
        config.dispatch.signing_key_id = Some("signing-key".into());
        config.dispatch.signing_key_file = Some("signing-key.pem".into());
        assert!(config.validate().is_ok());
        config.payload_protection.enabled = false;
        assert!(config.validate().is_err());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ManagedGatewayPayloadProtectionConfig {
    pub enabled: bool,
    pub transit_mount: String,
    pub transit_key_name: String,
}

impl Default for ManagedGatewayPayloadProtectionConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            transit_mount: "transit".into(),
            transit_key_name: "inari-managed-payload".into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayDispatchConfig {
    pub enabled: bool,
    pub epoch: u64,
    pub signing_key_id: Option<String>,
    pub signing_key_file: Option<PathBuf>,
    #[serde(with = "humantime_serde")]
    pub envelope_ttl: Duration,
}

impl Default for ManagedGatewayDispatchConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            epoch: 1,
            signing_key_id: None,
            signing_key_file: None,
            envelope_ttl: Duration::from_secs(2 * 60),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayOnboardingConfig {
    pub enabled: bool,
    pub public_base_url: Option<String>,
    #[serde(with = "humantime_serde")]
    pub invite_ttl: Duration,
    #[serde(with = "humantime_serde")]
    pub failed_attempt_window: Duration,
    pub max_failed_attempts: usize,
}

impl Default for ManagedGatewayOnboardingConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            public_base_url: None,
            invite_ttl: Duration::from_secs(10 * 60),
            failed_attempt_window: Duration::from_secs(60),
            max_failed_attempts: 5,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayDataPlaneConfig {
    pub connect_endpoints: Vec<String>,
    pub namespace_prefix: String,
    pub close_link_on_expiration: bool,
}

impl Default for ManagedGatewayDataPlaneConfig {
    fn default() -> Self {
        Self {
            connect_endpoints: Vec::new(),
            namespace_prefix: "iot/v1/agents".into(),
            close_link_on_expiration: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayCertificateConfig {
    pub mode: ManagedGatewayCertificateMode,
    pub step_ca_base_url: Option<Url>,
    pub step_ca_root_fingerprint: Option<String>,
    pub step_ca_provisioner: Option<String>,
    pub step_ca_key_id: Option<String>,
    pub step_ca_signing_key_file: Option<PathBuf>,
    pub step_ca_signing_algorithm: StepCaSigningAlgorithm,
    #[serde(with = "humantime_serde")]
    pub step_ca_token_ttl: Duration,
    pub requires_mutual_tls_after_issuance: bool,
}

impl Default for ManagedGatewayCertificateConfig {
    fn default() -> Self {
        Self {
            mode: ManagedGatewayCertificateMode::None,
            step_ca_base_url: None,
            step_ca_root_fingerprint: None,
            step_ca_provisioner: None,
            step_ca_key_id: None,
            step_ca_signing_key_file: None,
            step_ca_signing_algorithm: StepCaSigningAlgorithm::EdDsa,
            step_ca_token_ttl: Duration::from_secs(5 * 60),
            requires_mutual_tls_after_issuance: true,
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ManagedGatewayCertificateMode {
    #[default]
    None,
    StepCa,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepCaSigningAlgorithm {
    #[default]
    EdDsa,
    Es256,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn step_ca_requires_a_valid_root_pin_before_enrollment() {
        let mut config = ManagedGatewayConfig {
            enabled: true,
            router_policy: super::super::RouterPolicyConfig::test_config(),
            ..ManagedGatewayConfig::default()
        };
        config.data_plane.connect_endpoints = vec!["tls/router.example.com:7447".into()];
        config.certificate.mode = ManagedGatewayCertificateMode::StepCa;
        config.certificate.step_ca_base_url = Some(
            "https://ca.example.com"
                .parse()
                .unwrap(),
        );
        config.certificate.step_ca_provisioner = Some("agents".into());
        config.certificate.step_ca_key_id = Some("test-key".into());
        config
            .certificate
            .step_ca_signing_key_file = Some("/test/key.pem".into());
        for fingerprint in [None, Some("".into()), Some("z".repeat(64)), Some("a".repeat(63))] {
            config
                .certificate
                .step_ca_root_fingerprint = fingerprint;
            assert!(
                config
                    .validate()
                    .unwrap_err()
                    .to_string()
                    .contains("root_fingerprint")
            );
        }
        config
            .certificate
            .step_ca_root_fingerprint = Some("a".repeat(64));
        config.validate().unwrap();
        for url in [
            "http://ca.example.com",
            "https://user:password@ca.example.com",
            "https://ca.example.com?token=value",
            "https://ca.example.com#fragment",
        ] {
            config.certificate.step_ca_base_url = Some(url.parse().unwrap());
            assert!(config.validate().is_err());
        }
    }
}
