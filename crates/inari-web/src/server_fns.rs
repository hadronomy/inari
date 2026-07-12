use chrono::{DateTime, NaiveDate, Utc};
use leptos::prelude::*;
use serde::{Deserialize, Serialize};
use server_fn::codec::JsonEncoding;
use server_fn::error::{FromServerFnError, ServerFnErrorErr};
use url::Url;

#[cfg(feature = "ssr")]
#[derive(Clone, Debug)]
pub struct OnboardingContext(Option<inari_gateway::onboarding::OnboardingService>);

#[cfg(feature = "ssr")]
impl From<Option<inari_gateway::onboarding::OnboardingService>> for OnboardingContext {
    fn from(service: Option<inari_gateway::onboarding::OnboardingService>) -> Self {
        Self(service)
    }
}

#[cfg(not(feature = "ssr"))]
#[derive(Clone, Debug)]
pub struct OnboardingContext;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, thiserror::Error)]
#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
pub enum OnboardingError {
    #[error("Managed onboarding is not enabled on this controller.")]
    Disabled,
    #[error("Operator authentication failed.")]
    Forbidden,
    #[error("{0}")]
    InvalidRequest(String),
    #[error("The invitation was not found.")]
    NotFound,
    #[error("{0}")]
    Conflict(String),
    #[error("Managed onboarding is temporarily unavailable.")]
    Unavailable,
    #[error("The request could not be completed.")]
    Internal,
    #[error("The controller request failed.")]
    Transport,
}

impl FromServerFnError for OnboardingError {
    type Encoder = JsonEncoding;

    fn from_server_fn_error(_error: ServerFnErrorErr) -> Self {
        Self::Transport
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IssuedInvitation {
    pub invitation_id: String,
    pub invitation_url: Url,
    pub qr_data_uri: String,
    pub manual_code: String,
    pub expires_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvitationStatus {
    pub invitation_id: String,
    pub label: Option<String>,
    pub state: InvitationState,
    pub expires_at: DateTime<Utc>,
    pub agent_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvitationPreview {
    pub invitation_id: String,
    pub expires_at: DateTime<Utc>,
    pub state: InvitationState,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub supported_protocol_versions: Vec<NaiveDate>,
    pub certificate_mode: CertificateMode,
    pub requires_mutual_tls_after_issuance: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvitationState {
    Created,
    Claimed,
    Enrolled,
    Online,
    Expired,
    Failed,
    Revoked,
}

impl InvitationState {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Created => "Ready",
            Self::Claimed => "Claimed",
            Self::Enrolled => "Enrolled",
            Self::Online => "Online",
            Self::Expired => "Expired",
            Self::Failed => "Failed",
            Self::Revoked => "Revoked",
        }
    }

    pub const fn badge_class(self) -> &'static str {
        match self {
            Self::Created | Self::Claimed => "badge badge-pending",
            Self::Enrolled | Self::Online => "badge badge-positive",
            Self::Expired | Self::Revoked => "badge badge-muted",
            Self::Failed => "badge badge-negative",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CertificateMode {
    None,
    Controller,
    StepCa,
}

impl CertificateMode {
    pub const fn label(self) -> &'static str {
        match self {
            Self::None => "No managed certificate",
            Self::Controller => "Controller-issued certificate",
            Self::StepCa => "step-ca certificate",
        }
    }
}

#[server(prefix = "/_server/inari")]
pub async fn issue_invitation(
    operator_token: String,
    label: Option<String>,
) -> Result<IssuedInvitation, OnboardingError> {
    use self::ssr::*;

    let invitation = onboarding()?
        .create_invitation(
            SecretString::from(operator_token),
            inari_gateway::onboarding::CreateInvitation { label },
        )
        .await
        .map_err(onboarding_error)?;
    let qr_data_uri = qr_data_uri(invitation.invitation_url.as_str()).map_err(internal_error)?;
    Ok(IssuedInvitation {
        invitation_id: invitation.invitation_id.to_string(),
        invitation_url: invitation.invitation_url,
        qr_data_uri,
        manual_code: invitation.manual_code,
        expires_at: invitation.expires_at,
    })
}

#[server(prefix = "/_server/inari")]
pub async fn load_invitations(
    operator_token: String,
) -> Result<Vec<InvitationStatus>, OnboardingError> {
    use self::ssr::*;

    onboarding()?
        .invitations(SecretString::from(operator_token))
        .await
        .map(|invitations| {
            invitations
                .into_iter()
                .map(InvitationStatus::from)
                .collect()
        })
        .map_err(onboarding_error)
}

#[server(prefix = "/_server/inari")]
pub async fn revoke_invitation(
    operator_token: String,
    invitation_id: String,
) -> Result<InvitationStatus, OnboardingError> {
    use self::ssr::*;

    let invitation_id = invitation_id
        .parse()
        .map_err(onboarding_error)?;
    onboarding()?
        .revoke_invitation(SecretString::from(operator_token), &invitation_id)
        .await
        .map(InvitationStatus::from)
        .map_err(onboarding_error)
}

#[server(prefix = "/_server/inari")]
pub async fn load_invitation(invitation_id: String) -> Result<InvitationPreview, OnboardingError> {
    use self::ssr::*;

    let invitation_id = invitation_id
        .parse()
        .map_err(onboarding_error)?;
    onboarding()?
        .invitation_preview(&invitation_id)
        .await
        .map(InvitationPreview::from)
        .map_err(onboarding_error)
}

#[cfg(feature = "ssr")]
mod ssr {
    pub(super) use base64::Engine;
    pub(super) use leptos::prelude::*;
    pub(super) use qrcode::{QrCode, render::svg};
    pub(super) use secrecy::SecretString;

    use http::StatusCode;
    use inari_gateway::onboarding::OnboardingService;
    use leptos_axum::ResponseOptions;

    use super::{
        CertificateMode, InvitationPreview, InvitationState, InvitationStatus, OnboardingContext,
        OnboardingError,
    };

    pub(super) fn onboarding() -> Result<OnboardingService, OnboardingError> {
        let Some(context) = use_context::<OnboardingContext>() else {
            return Err(internal_error("Leptos onboarding context was not provided"));
        };
        context.0.ok_or_else(|| {
            error_response(OnboardingError::Disabled, StatusCode::SERVICE_UNAVAILABLE)
        })
    }

    pub(super) fn onboarding_error(error: inari_gateway::GatewayError) -> OnboardingError {
        use inari_gateway::GatewayError;

        match error {
            GatewayError::InvalidInput(detail) => {
                error_response(OnboardingError::InvalidRequest(detail), StatusCode::BAD_REQUEST)
            },
            GatewayError::Forbidden(_) => {
                error_response(OnboardingError::Forbidden, StatusCode::FORBIDDEN)
            },
            GatewayError::NotFound(_) => {
                error_response(OnboardingError::NotFound, StatusCode::NOT_FOUND)
            },
            GatewayError::Conflict(detail) => {
                error_response(OnboardingError::Conflict(detail), StatusCode::CONFLICT)
            },
            GatewayError::Unavailable(_) => {
                error_response(OnboardingError::Unavailable, StatusCode::SERVICE_UNAVAILABLE)
            },
            error => internal_error(error),
        }
    }

    pub(super) fn internal_error(error: impl std::fmt::Display) -> OnboardingError {
        tracing::error!(error = %error, "managed onboarding request failed");
        error_response(OnboardingError::Internal, StatusCode::INTERNAL_SERVER_ERROR)
    }

    fn error_response(error: OnboardingError, status: StatusCode) -> OnboardingError {
        if let Some(response) = use_context::<ResponseOptions>() {
            response.set_status(status);
        }
        error
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
}
