use std::path::PathBuf;
use std::time::Duration;

use inari_gateway::credentials::TokenDigest;
use inari_gateway::protocol::ProtocolVersion;
use serde::{Deserialize, Serialize};
use url::Url;
use zenoh::key_expr::OwnedKeyExpr;

use crate::error::ConfigError;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayConfig {
    pub enabled: bool,
    pub database_path: PathBuf,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub enrollment_token_hashes: Vec<TokenDigest>,
    pub supported_protocol_versions: Vec<ProtocolVersion>,
    pub controller_actions: Vec<String>,
    pub api: ManagedGatewayApiConfig,
    pub onboarding: ManagedGatewayOnboardingConfig,
    pub data_plane: ManagedGatewayDataPlaneConfig,
    pub certificate: ManagedGatewayCertificateConfig,
}

impl Default for ManagedGatewayConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            database_path: PathBuf::from("data/inari-server/managed-gateway.sqlite3"),
            controller_name: Some("Inari Controller".into()),
            controller_instance_id: "inari-server".into(),
            enrollment_token_hashes: Vec::new(),
            supported_protocol_versions: vec![ProtocolVersion::current()],
            controller_actions: [
                "system:read",
                "devices:read",
                "events:read",
                "jobs:create",
                "jobs:cancel",
                "commands:execute",
            ]
            .map(String::from)
            .to_vec(),
            api: ManagedGatewayApiConfig::default(),
            onboarding: ManagedGatewayOnboardingConfig::default(),
            data_plane: ManagedGatewayDataPlaneConfig::default(),
            certificate: ManagedGatewayCertificateConfig::default(),
        }
    }
}

impl ManagedGatewayConfig {
    pub(super) fn validate(&self) -> Result<(), ConfigError> {
        if !self.enabled {
            return Ok(());
        }
        if self
            .database_path
            .as_os_str()
            .is_empty()
        {
            return Err(ConfigError::invalid("managed_gateway.database_path must not be empty."));
        }
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
            if self
                .onboarding
                .operator_token_hashes
                .is_empty()
            {
                return Err(ConfigError::invalid(
                    "managed_gateway.onboarding.operator_token_hashes must not be empty when onboarding is enabled.",
                ));
            }
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
        Ok(())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayApiConfig {
    pub read_token_hashes: Vec<TokenDigest>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct ManagedGatewayOnboardingConfig {
    pub enabled: bool,
    pub public_base_url: Option<String>,
    pub operator_token_hashes: Vec<TokenDigest>,
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
            operator_token_hashes: Vec::new(),
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
    pub step_ca_base_url: Option<String>,
    pub step_ca_root_fingerprint: Option<String>,
    pub step_ca_bootstrap_ott: Option<String>,
    pub step_ca_bootstrap_expires_at: Option<chrono::DateTime<chrono::Utc>>,
    pub step_ca_authorized_sans: Vec<String>,
    pub requires_mutual_tls_after_issuance: bool,
}

impl Default for ManagedGatewayCertificateConfig {
    fn default() -> Self {
        Self {
            mode: ManagedGatewayCertificateMode::None,
            step_ca_base_url: None,
            step_ca_root_fingerprint: None,
            step_ca_bootstrap_ott: None,
            step_ca_bootstrap_expires_at: None,
            step_ca_authorized_sans: Vec::new(),
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
