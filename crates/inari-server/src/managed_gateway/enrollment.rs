use std::collections::BTreeSet;
use std::str::FromStr;

use chrono::{DateTime, Utc};
use inari_gateway::certificate::CertificateRequest;
use inari_gateway::protocol::{
    AgentId, CertificateProvisioning, CertificateTrust, ControllerInfo, DataPlane, DataPlaneAuth,
    DataPlaneAuthKind, DataPlaneKind, DataPlaneTls, EnrollmentPermissions, EnrollmentRequest,
    EnrollmentResponse, ProtocolVersion, Serialization, SessionMode, StepCaEnrollment,
};
use inari_gateway::security::validate_identity;

use super::{ManagedGatewayController, StoredAgentEnrollment};
use crate::config::ManagedGatewayCertificateMode;
use crate::error::{AppError, AppResult};
use crate::zenoh::KeyExpression;

impl ManagedGatewayController {
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
        let now = Utc::now();
        let invitation_id = self
            .authenticate_enrollment_credential(
                bearer_token,
                request.agent_id.as_str(),
                &request.key_id,
                now,
            )
            .await?;
        let certificate = self.certificate_payload(&request.agent_id, &identity.csr_fingerprint)?;

        let enrollment = StoredAgentEnrollment {
            agent_id: request.agent_id.to_string(),
            organization_id: self.inner.organization.id.clone(),
            site_id: self
                .inner
                .organization
                .default_site_id
                .clone(),
            key_id: request.key_id.clone(),
            public_jwk_fingerprint: identity.jwk_thumbprint,
            public_jwk: request.public_jwk.clone(),
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
            .enroll(enrollment, invitation_id, request.snapshot.clone())
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

    async fn authenticate_enrollment_credential(
        &self,
        bearer_token: Option<&str>,
        agent_id: &str,
        key_id: &str,
        now: DateTime<Utc>,
    ) -> AppResult<String> {
        let Some(token) = bearer_token else {
            return Err(AppError::forbidden("Enrollment requires an invitation credential."));
        };
        if !self.inner.config.onboarding.enabled {
            return Err(AppError::service_unavailable(
                "Invitation enrollment is not enabled on this controller.",
            ));
        }
        let code = token.parse::<inari_gateway::onboarding::InvitationCode>()?;
        let invitation_id = code.id().to_string();
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
        Ok(invitation_id)
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

    fn certificate_payload(
        &self,
        agent_id: &AgentId,
        csr_fingerprint: &str,
    ) -> AppResult<Option<CertificateProvisioning>> {
        let certificate = &self.inner.config.certificate;
        match certificate.mode {
            ManagedGatewayCertificateMode::None => Ok(None),
            ManagedGatewayCertificateMode::StepCa => {
                let base_url = certificate
                    .step_ca_base_url
                    .as_ref()
                    .ok_or_else(|| {
                        AppError::service_unavailable("step-ca base URL is not configured.")
                    })?
                    .to_string();
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
                let bootstrap_auth = self
                    .inner
                    .certificate_issuer
                    .as_ref()
                    .ok_or_else(|| {
                        AppError::service_unavailable("step-ca token issuer is unavailable.")
                    })?
                    .issue(&CertificateRequest {
                        agent_id: agent_id.clone(),
                        authorized_sans: authorized_sans.clone(),
                        csr_fingerprint: csr_fingerprint.to_owned(),
                    })?;
                Ok(Some(CertificateProvisioning::StepCa {
                    enrollment: StepCaEnrollment {
                        base_url,
                        trust: CertificateTrust {
                            root_fingerprint: certificate
                                .step_ca_root_fingerprint
                                .clone(),
                        },
                        bootstrap_auth: Some(bootstrap_auth),
                        subject: Some(agent_id.to_string()),
                        authorized_sans,
                        requires_mutual_tls_after_issuance: certificate
                            .requires_mutual_tls_after_issuance,
                    },
                }))
            },
        }
    }
}
