use std::collections::BTreeSet;
use std::str::FromStr;
use std::sync::Arc;

use bytes::Bytes;
use chrono::{DateTime, Utc};
use inari_gateway::GatewayRepository;
use inari_gateway::credentials::TokenVerifier;
use inari_gateway::protocol::{
    AgentId, AgentStatus, CertificateBootstrapAuth, CertificateBootstrapAuthKind,
    CertificateProvisioning, CertificateTrust, ControllerInfo, DataPlane, DataPlaneAuth,
    DataPlaneAuthKind, DataPlaneKind, DataPlaneTls, EnrollmentPermissions, EnrollmentRequest,
    EnrollmentResponse, ProtocolVersion, Serialization, SessionMode, StepCaEnrollment,
};
use inari_gateway::security::validate_identity;
use secrecy::SecretString;
use sha2::{Digest, Sha256};
use zenoh::bytes::Encoding;

use crate::config::{ManagedGatewayCertificateMode, ManagedGatewayConfig, ZenohConfig};
use crate::error::{AppError, AppResult};
use crate::zenoh::{KeyExpression, ZenohHandle};

mod models;
mod runtime;
mod store;

pub use self::models::{
    AgentPublicationList, CommandHistoryResponse, SubmitControllerCommandRequest,
    SubmitControllerCommandResponse,
};
use self::models::{
    ControllerCommandState, EnrollmentCredential, StoredAgentEnrollment, StoredControllerCommand,
};
use self::store::ManagedGatewayStore;

#[derive(Clone)]
pub struct ManagedGatewayController {
    inner: Arc<ManagedGatewayControllerInner>,
}

struct ManagedGatewayControllerInner {
    config: ManagedGatewayConfig,
    zenoh_config: ZenohConfig,
    zenoh: ZenohHandle,
    store: ManagedGatewayStore,
    enrollment_tokens: TokenVerifier,
    read_api_tokens: TokenVerifier,
}

impl ManagedGatewayController {
    #[must_use]
    pub fn new(
        config: ManagedGatewayConfig,
        zenoh_config: ZenohConfig,
        zenoh: ZenohHandle,
        repository: GatewayRepository,
    ) -> Self {
        let store = ManagedGatewayStore::new(repository);
        let enrollment_tokens = TokenVerifier::new(config.enrollment_token_hashes.clone());
        let read_api_tokens = TokenVerifier::new(config.api.read_token_hashes.clone());
        Self {
            inner: Arc::new(ManagedGatewayControllerInner {
                config,
                zenoh_config,
                zenoh,
                store,
                enrollment_tokens,
                read_api_tokens,
            }),
        }
    }

    #[must_use]
    pub fn is_enabled(&self) -> bool {
        self.inner.config.enabled
    }

    pub async fn enroll(
        &self,
        bearer_token: Option<&str>,
        request: EnrollmentRequest,
    ) -> AppResult<EnrollmentResponse> {
        self.ensure_enabled()?;
        if request.key_id.trim().is_empty() {
            return Err(AppError::bad_request("Enrollment key_id is required."));
        }
        let identity = validate_identity(
            &request.key_id,
            &request.public_jwk,
            &request.csr_pem,
            request.certificate_pem.as_deref(),
        )?;

        let selected_protocol_version = self.select_protocol_version(&request)?;
        let namespace = self.namespace_for_agent(request.agent_id.as_str())?;
        let connect_endpoints = self.data_plane_connect_endpoints()?;
        let certificate = self.certificate_payload(request.agent_id.as_str());
        let now = Utc::now();
        let credential = self
            .authenticate_enrollment_credential(
                bearer_token,
                request.agent_id.as_str(),
                &request.key_id,
                now,
            )
            .await?;

        let enrollment = StoredAgentEnrollment {
            agent_id: request.agent_id.to_string(),
            key_id: request.key_id.clone(),
            public_jwk_fingerprint: identity.jwk_thumbprint,
            public_jwk: serde_json::to_value(&request.public_jwk)?,
            certificate_pem: request.certificate_pem.clone(),
            namespace: namespace.clone(),
            protocol_version: selected_protocol_version.clone(),
            controller_actions: self
                .inner
                .config
                .controller_actions
                .clone(),
            enrolled_at: now,
        };

        self.inner
            .store
            .enroll(enrollment, credential, serde_json::to_value(&request.snapshot)?)
            .await?;

        Ok(EnrollmentResponse {
            selected_protocol_version,
            controller: ControllerInfo {
                name: self
                    .inner
                    .config
                    .controller_name
                    .clone(),
                instance_id: self
                    .inner
                    .config
                    .controller_instance_id
                    .clone(),
            },
            permissions: EnrollmentPermissions {
                controller_actions: self
                    .inner
                    .config
                    .controller_actions
                    .clone(),
            },
            data_plane: DataPlane {
                kind: DataPlaneKind::Zenoh,
                session_mode: SessionMode::Client,
                connect_endpoints,
                namespace,
                serialization: Serialization::Json,
                auth: DataPlaneAuth { kind: DataPlaneAuthKind::Mtls },
                tls: DataPlaneTls {
                    close_link_on_expiration: self
                        .inner
                        .config
                        .data_plane
                        .close_link_on_expiration,
                },
            },
            certificate,
            enrolled_at: now,
        })
    }

    pub async fn enqueue_command(
        &self,
        request: SubmitControllerCommandRequest,
    ) -> AppResult<SubmitControllerCommandResponse> {
        self.ensure_enabled()?;
        if request.agent_id.trim().is_empty() {
            return Err(AppError::bad_request("Command agent_id is required."));
        }

        let command = self
            .inner
            .store
            .enqueue_command(request, &self.inner.config.controller_actions)
            .await?;

        let publish_result = self
            .publish_live_command(&command)
            .await;
        if publish_result.is_ok() {
            self.inner
                .store
                .mark_command_published(&command.agent_id, &command.command_id)
                .await?;
        } else if let Err(error) = &publish_result {
            tracing::debug!(
                error = %error,
                command_id = %command.command_id,
                agent_id = %command.agent_id,
                "queued managed gateway command could not be published live"
            );
        }

        Ok(SubmitControllerCommandResponse {
            command: command.command,
            state: if publish_result.is_ok() {
                ControllerCommandState::Published
            } else {
                ControllerCommandState::Queued
            },
        })
    }

    pub async fn list_commands(&self, agent_id: &str) -> AppResult<CommandHistoryResponse> {
        self.ensure_enabled()?;
        self.inner
            .store
            .command_history(agent_id, 1)
            .await
    }

    pub async fn list_publications(&self, agent_id: &str) -> AppResult<AgentPublicationList> {
        self.ensure_enabled()?;
        self.inner
            .store
            .list_publications(agent_id)
            .await
    }

    pub fn authorize_read_api(&self, token: SecretString) -> AppResult<()> {
        self.ensure_enabled()?;
        if !self
            .inner
            .read_api_tokens
            .is_configured()
        {
            return Err(AppError::service_unavailable(
                "Managed gateway read API authentication is not configured.",
            ));
        }
        if self
            .inner
            .read_api_tokens
            .accepts(&token)
        {
            Ok(())
        } else {
            Err(AppError::unauthorized("The API bearer token was not accepted."))
        }
    }

    pub async fn agent_status(&self, agent_id: &AgentId) -> AppResult<AgentStatus> {
        self.ensure_enabled()?;
        self.inner
            .store
            .latest_status(agent_id)
            .await?
            .ok_or_else(|| AppError::not_found("No status has been observed for this agent."))
    }

    fn ensure_enabled(&self) -> AppResult<()> {
        if self.inner.config.enabled {
            Ok(())
        } else {
            Err(AppError::service_unavailable("Managed gateway controller is not enabled."))
        }
    }

    async fn authenticate_enrollment_credential(
        &self,
        bearer_token: Option<&str>,
        agent_id: &str,
        key_id: &str,
        now: DateTime<Utc>,
    ) -> AppResult<EnrollmentCredential> {
        let Some(token) = bearer_token else {
            return Err(AppError::forbidden("Enrollment requires a bearer token."));
        };
        let secret = SecretString::from(token.to_owned());
        let token_hash = sha256_hex(token);
        if self
            .inner
            .enrollment_tokens
            .accepts(&secret)
        {
            return Ok(EnrollmentCredential::ConfiguredToken { token_hash });
        }

        if self.inner.config.onboarding.enabled {
            let code = token.parse::<inari_gateway::onboarding::InvitationCode>()?;
            let invite_id = code.id().to_string();
            self.inner
                .store
                .repository
                .claim_invitation(
                    &code,
                    agent_id,
                    key_id,
                    now,
                    self.inner
                        .config
                        .onboarding
                        .failed_attempt_window,
                    self.inner
                        .config
                        .onboarding
                        .max_failed_attempts,
                )
                .await?;
            return Ok(EnrollmentCredential::Invite { invite_id });
        }
        Err(AppError::forbidden("Enrollment token was not accepted."))
    }

    fn select_protocol_version(&self, request: &EnrollmentRequest) -> AppResult<ProtocolVersion> {
        let supported_by_agent = request
            .protocol
            .supported_versions
            .iter()
            .cloned()
            .collect::<BTreeSet<_>>();
        self.inner
            .config
            .supported_protocol_versions
            .iter()
            .find(|version| supported_by_agent.contains(*version))
            .cloned()
            .ok_or_else(|| {
                AppError::bad_request(
                    "Agent does not support any configured gateway protocol version.",
                )
            })
    }

    fn data_plane_connect_endpoints(&self) -> AppResult<Vec<String>> {
        if self
            .inner
            .config
            .data_plane
            .connect_endpoints
            .is_empty()
        {
            return Err(AppError::service_unavailable(
                "Managed gateway data-plane connect endpoints are not configured.",
            ));
        }
        Ok(self
            .inner
            .config
            .data_plane
            .connect_endpoints
            .clone())
    }

    fn namespace_for_agent(&self, agent_id: &str) -> AppResult<String> {
        let prefix = KeyExpression::from_str(
            self.inner
                .config
                .data_plane
                .namespace_prefix
                .trim_end_matches('/'),
        )
        .map_err(|source| AppError::bad_request(format!("Invalid namespace prefix: {source}")))?;
        prefix
            .join(agent_id)
            .map(|key| key.to_string())
            .map_err(|source| AppError::bad_request(format!("Invalid agent namespace: {source}")))
    }

    fn certificate_payload(&self, agent_id: &str) -> Option<CertificateProvisioning> {
        let certificate = &self.inner.config.certificate;
        match certificate.mode {
            ManagedGatewayCertificateMode::None => None,
            ManagedGatewayCertificateMode::StepCa => {
                let base_url = certificate.step_ca_base_url.clone()?;
                let authorized_sans = if certificate
                    .step_ca_authorized_sans
                    .is_empty()
                {
                    vec![format!("urn:inari:{agent_id}")]
                } else {
                    certificate
                        .step_ca_authorized_sans
                        .clone()
                };
                Some(CertificateProvisioning::StepCa {
                    enrollment: StepCaEnrollment {
                        base_url,
                        trust: CertificateTrust {
                            root_fingerprint: certificate
                                .step_ca_root_fingerprint
                                .clone(),
                        },
                        bootstrap_auth: certificate
                            .step_ca_bootstrap_ott
                            .clone()
                            .map(|token| CertificateBootstrapAuth {
                                kind: CertificateBootstrapAuthKind::Ott,
                                token: Some(token),
                                expires_at: certificate.step_ca_bootstrap_expires_at,
                            }),
                        subject: Some(agent_id.to_owned()),
                        authorized_sans,
                        requires_mutual_tls_after_issuance: certificate
                            .requires_mutual_tls_after_issuance,
                    },
                })
            },
        }
    }

    async fn publish_live_command(&self, command: &StoredControllerCommand) -> AppResult<()> {
        let namespace =
            KeyExpression::from_str(command.namespace.trim_end_matches('/')).map_err(|source| {
                AppError::bad_request(format!("Invalid Zenoh command key: {source}"))
            })?;
        let key = namespace
            .join("commands")
            .and_then(|key| key.join("live"))
            .and_then(|key| key.join(&command.command_id))
            .map_err(|source| {
                AppError::bad_request(format!("Invalid Zenoh command key: {source}"))
            })?;
        let payload = serde_json::to_vec(&command.command).map_err(|source| {
            AppError::internal(
                "managed_gateway_command_serialization",
                "Failed to serialize controller command.",
            )
            .with_source(source)
        })?;
        self.inner
            .zenoh
            .put_bytes(key, Bytes::from(payload), Encoding::APPLICATION_JSON, None)
            .await
    }

    fn history_query_key(&self) -> AppResult<KeyExpression> {
        self.namespace_prefix_key()?
            .join("*")
            .and_then(|key| key.join("commands"))
            .and_then(|key| key.join("history"))
            .map_err(|source| {
                AppError::bad_request(format!("Invalid managed history key expression: {source}"))
            })
    }

    fn publications_key(&self) -> AppResult<KeyExpression> {
        self.namespace_prefix_key()?
            .join("*")
            .and_then(|key| key.join("**"))
            .map_err(|source| {
                AppError::bad_request(format!(
                    "Invalid managed publication key expression: {source}"
                ))
            })
    }

    fn namespace_prefix_key(&self) -> AppResult<KeyExpression> {
        KeyExpression::from_str(
            self.inner
                .config
                .data_plane
                .namespace_prefix
                .trim_end_matches('/'),
        )
        .map_err(|source| {
            AppError::bad_request(format!("Invalid managed namespace prefix: {source}"))
        })
    }

    fn agent_id_from_key(&self, key: &str) -> Option<String> {
        let prefix = self
            .inner
            .config
            .data_plane
            .namespace_prefix
            .trim_end_matches('/');
        let rest = key
            .strip_prefix(prefix)?
            .strip_prefix('/')?;
        rest.split('/')
            .next()
            .map(str::to_owned)
    }
}

fn sha256_hex(value: &str) -> String {
    hex::encode(Sha256::digest(value.as_bytes()))
}
