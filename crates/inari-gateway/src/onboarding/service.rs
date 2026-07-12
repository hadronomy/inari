use std::time::Duration;

use chrono::Utc;
use secrecy::SecretString;
use url::Url;

use super::{
    CertificateMode, CreateInvitation, InvitationCode, InvitationId, InvitationPreview,
    InvitationState, InvitationStatus, IssuedInvitation,
};
use crate::credentials::{TokenDigest, TokenVerifier};
use crate::protocol::ProtocolVersion;
use crate::{GatewayError, GatewayRepository, GatewayResult};

#[derive(Debug, Clone)]
pub struct OnboardingConfig {
    pub database_path: std::path::PathBuf,
    pub enabled: bool,
    pub public_base_url: Option<Url>,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub operator_token_hashes: Vec<TokenDigest>,
    pub invitation_ttl: Duration,
    pub supported_protocol_versions: Vec<ProtocolVersion>,
    pub certificate_mode: CertificateMode,
    pub requires_mutual_tls_after_issuance: bool,
}

#[derive(Clone, Debug)]
pub struct OnboardingService {
    repository: GatewayRepository,
    enabled: bool,
    public_base_url: Option<Url>,
    controller_name: Option<String>,
    controller_instance_id: String,
    operator_tokens: TokenVerifier,
    invitation_ttl: Duration,
    supported_protocol_versions: Vec<ProtocolVersion>,
    certificate_mode: CertificateMode,
    requires_mutual_tls_after_issuance: bool,
}

impl OnboardingService {
    pub async fn initialize(config: OnboardingConfig) -> GatewayResult<Self> {
        let repository = GatewayRepository::connect(&config.database_path).await?;
        Ok(Self {
            repository,
            enabled: config.enabled,
            public_base_url: config
                .public_base_url
                .map(normalize_base_url),
            controller_name: config.controller_name,
            controller_instance_id: config.controller_instance_id,
            operator_tokens: TokenVerifier::new(config.operator_token_hashes),
            invitation_ttl: config.invitation_ttl,
            supported_protocol_versions: if config
                .supported_protocol_versions
                .is_empty()
            {
                vec![ProtocolVersion::current()]
            } else {
                config.supported_protocol_versions
            },
            certificate_mode: config.certificate_mode,
            requires_mutual_tls_after_issuance: config.requires_mutual_tls_after_issuance,
        })
    }

    #[must_use]
    pub fn repository(&self) -> &GatewayRepository {
        &self.repository
    }

    pub fn authenticate_operator(&self, token: &SecretString) -> GatewayResult<()> {
        self.ensure_onboarding_enabled()?;
        if self.operator_tokens.accepts(token) {
            Ok(())
        } else {
            Err(GatewayError::Forbidden(
                "onboarding administration requires operator authentication".into(),
            ))
        }
    }

    pub async fn create_invitation(
        &self,
        operator_token: SecretString,
        request: CreateInvitation,
    ) -> GatewayResult<IssuedInvitation> {
        self.authenticate_operator(&operator_token)?;
        let label = request
            .label
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty());
        if label
            .as_ref()
            .is_some_and(|value| value.chars().count() > 120)
        {
            return Err(GatewayError::InvalidInput(
                "invitation labels must be 120 characters or fewer".into(),
            ));
        }
        let code = InvitationCode::generate()?;
        let created_at = Utc::now();
        let expires_at = created_at
            + chrono::Duration::from_std(self.invitation_ttl)
                .map_err(|_| GatewayError::InvalidInput("invitation TTL is out of range".into()))?;
        self.repository
            .create_invitation(&code, label.as_deref(), created_at, expires_at)
            .await?;
        let mut invitation_url = self
            .public_base_url
            .as_ref()
            .ok_or_else(|| {
                GatewayError::Unavailable(
                    "managed onboarding public_base_url is not configured".into(),
                )
            })?
            .join(&format!("setup/{}", code.id()))
            .map_err(|error| {
                GatewayError::InvalidInput(format!("invalid public onboarding URL: {error}"))
            })?;
        invitation_url.set_fragment(Some(&format!("code={}", code.manual())));
        Ok(IssuedInvitation {
            invitation_id: code.id().clone(),
            invitation_url,
            manual_code: code.manual(),
            expires_at,
            state: InvitationState::Created,
        })
    }

    pub async fn invitations(
        &self,
        operator_token: SecretString,
    ) -> GatewayResult<Vec<InvitationStatus>> {
        self.authenticate_operator(&operator_token)?;
        self.repository
            .invitations(Utc::now())
            .await
    }

    pub async fn revoke_invitation(
        &self,
        operator_token: SecretString,
        invitation_id: &InvitationId,
    ) -> GatewayResult<InvitationStatus> {
        self.authenticate_operator(&operator_token)?;
        self.repository
            .revoke_invitation(invitation_id.as_str(), Utc::now())
            .await
    }

    pub async fn invitation_preview(
        &self,
        invitation_id: &InvitationId,
    ) -> GatewayResult<InvitationPreview> {
        self.ensure_onboarding_enabled()?;
        let invitation = self
            .repository
            .invitation(invitation_id.as_str(), Utc::now())
            .await?;
        Ok(InvitationPreview {
            invitation_id: invitation.invitation_id,
            expires_at: invitation.expires_at,
            state: invitation.state,
            controller_name: self.controller_name.clone(),
            controller_instance_id: self.controller_instance_id.clone(),
            supported_protocol_versions: self.supported_protocol_versions.clone(),
            certificate_mode: self.certificate_mode,
            requires_mutual_tls_after_issuance: self.requires_mutual_tls_after_issuance,
        })
    }

    fn ensure_onboarding_enabled(&self) -> GatewayResult<()> {
        if self.enabled {
            Ok(())
        } else {
            Err(GatewayError::Unavailable("managed gateway onboarding is not enabled".into()))
        }
    }
}

fn normalize_base_url(mut url: Url) -> Url {
    if !url.path().ends_with('/') {
        let path = format!("{}/", url.path());
        url.set_path(&path);
    }
    url.set_query(None);
    url.set_fragment(None);
    url
}
