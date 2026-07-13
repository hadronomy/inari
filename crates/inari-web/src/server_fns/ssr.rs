pub(super) use base64::Engine;
pub(super) use leptos::prelude::*;
pub(super) use qrcode::{QrCode, render::svg};

pub(super) use inari_gateway::identity::Permission;
use inari_gateway::identity::SessionIdentity;
use inari_gateway::onboarding::OnboardingService;
pub(super) use inari_gateway::protocol::AgentHealthState;
use tower_sessions::Session;

use super::{
    CertificateMode, InvitationPreview, InvitationState, InvitationStatus, OnboardingContext,
    OnboardingError,
};

pub(super) fn onboarding() -> Result<OnboardingService, OnboardingError> {
    let Some(context) = use_context::<OnboardingContext>() else {
        return Err(internal_error("Leptos onboarding context was not provided"));
    };
    context
        .service()
        .ok_or(OnboardingError::Disabled)
}

pub(super) async fn require_permission(
    permission: Permission,
) -> Result<SessionIdentity, OnboardingError> {
    let session = leptos_axum::extract::<Session>()
        .await
        .map_err(|error| internal_error(format!("session extraction failed: {error}")))?;
    let identity = session
        .get::<SessionIdentity>("identity")
        .await
        .map_err(internal_error)?
        .ok_or(OnboardingError::Forbidden)?;
    if identity.expires_at <= chrono::Utc::now() {
        session
            .flush()
            .await
            .map_err(internal_error)?;
        return Err(OnboardingError::Forbidden);
    }
    if identity.grants(permission) { Ok(identity) } else { Err(OnboardingError::Forbidden) }
}

pub(super) fn audit_context(identity: &SessionIdentity) -> inari_gateway::audit::AuditContext {
    inari_gateway::audit::AuditContext::new(identity.actor_id.clone(), None)
}

pub(super) fn onboarding_error(error: inari_gateway::GatewayError) -> OnboardingError {
    use inari_gateway::GatewayError;

    match error {
        GatewayError::InvalidInput(detail) => OnboardingError::InvalidRequest(detail),
        GatewayError::Forbidden(_) => OnboardingError::Forbidden,
        GatewayError::NotFound(_) => OnboardingError::NotFound,
        GatewayError::Conflict(detail) => OnboardingError::Conflict(detail),
        GatewayError::Unavailable(_) => OnboardingError::Unavailable,
        error => internal_error(error),
    }
}

pub(super) fn internal_error(error: impl std::fmt::Display) -> OnboardingError {
    tracing::error!(error = %error, "managed onboarding request failed");
    OnboardingError::Internal
}

pub(super) fn qr_data_uri(value: &str) -> Result<String, String> {
    let code = QrCode::new(value.as_bytes()).map_err(|error| error.to_string())?;
    let image = code
        .render()
        .min_dimensions(280, 280)
        .dark_color(svg::Color("#17211d"))
        .light_color(svg::Color("#ffffff"))
        .build();
    let encoded = base64::engine::general_purpose::STANDARD.encode(image.as_bytes());
    Ok(format!("data:image/svg+xml;base64,{encoded}"))
}

impl From<inari_gateway::onboarding::InvitationStatus> for InvitationStatus {
    fn from(invitation: inari_gateway::onboarding::InvitationStatus) -> Self {
        Self {
            invitation_id: invitation.invitation_id.to_string(),
            site_id: invitation.site_id.to_string(),
            label: invitation.label,
            state: invitation.state.into(),
            expires_at: invitation.expires_at,
            agent_id: invitation.agent_id,
        }
    }
}

impl From<inari_gateway::onboarding::InvitationPreview> for InvitationPreview {
    fn from(preview: inari_gateway::onboarding::InvitationPreview) -> Self {
        Self {
            invitation_id: preview.invitation_id.to_string(),
            site_id: preview.site_id.to_string(),
            expires_at: preview.expires_at,
            state: preview.state.into(),
            controller_name: preview.controller_name,
            controller_instance_id: preview.controller_instance_id,
            supported_protocol_versions: preview
                .supported_protocol_versions
                .into_iter()
                .map(|version| {
                    version
                        .as_str()
                        .parse()
                        .expect("gateway protocol versions are validated dates")
                })
                .collect(),
            certificate_mode: match preview.certificate_mode {
                inari_gateway::onboarding::CertificateMode::None => CertificateMode::None,
                inari_gateway::onboarding::CertificateMode::Controller => {
                    CertificateMode::Controller
                },
                inari_gateway::onboarding::CertificateMode::StepCa => CertificateMode::StepCa,
            },
            requires_mutual_tls_after_issuance: preview.requires_mutual_tls_after_issuance,
        }
    }
}

impl From<inari_gateway::onboarding::InvitationState> for InvitationState {
    fn from(state: inari_gateway::onboarding::InvitationState) -> Self {
        match state {
            inari_gateway::onboarding::InvitationState::Created => Self::Created,
            inari_gateway::onboarding::InvitationState::Claimed => Self::Claimed,
            inari_gateway::onboarding::InvitationState::Enrolled => Self::Enrolled,
            inari_gateway::onboarding::InvitationState::Online => Self::Online,
            inari_gateway::onboarding::InvitationState::Expired => Self::Expired,
            inari_gateway::onboarding::InvitationState::Failed => Self::Failed,
            inari_gateway::onboarding::InvitationState::Revoked => Self::Revoked,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disabled_onboarding_returns_a_typed_error() {
        let owner = Owner::new();
        owner.with(|| {
            provide_context(OnboardingContext::from(None));
            assert_eq!(
                onboarding().expect_err("onboarding should be disabled"),
                OnboardingError::Disabled
            );
        });
    }

    #[test]
    fn operator_failures_do_not_expose_gateway_details() {
        let error = onboarding_error(inari_gateway::GatewayError::Forbidden(
            "sensitive authentication detail".into(),
        ));
        assert_eq!(error, OnboardingError::Forbidden);
        assert!(!error.to_string().contains("sensitive"));
    }
}
