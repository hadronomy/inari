use chrono::{DateTime, NaiveDate, Utc};
use leptos::prelude::*;
use serde::{Deserialize, Serialize};
use server_fn::codec::JsonEncoding;
use server_fn::error::{FromServerFnError, ServerFnErrorErr};
use url::Url;

#[cfg(feature = "ssr")]
mod context {
    use std::fmt;
    use std::sync::Arc;

    use inari_gateway::onboarding::OnboardingService;

    use super::{ControllerSnapshot, DeploymentEnvironment};

    #[derive(Clone, Debug)]
    pub struct OnboardingContext(Option<OnboardingService>);

    impl OnboardingContext {
        pub(super) fn service(&self) -> Option<OnboardingService> {
            self.0.clone()
        }
    }

    impl From<Option<OnboardingService>> for OnboardingContext {
        fn from(service: Option<OnboardingService>) -> Self {
            Self(service)
        }
    }

    #[derive(Clone)]
    pub struct ControllerContext {
        environment: DeploymentEnvironment,
        snapshot: Arc<dyn Fn() -> ControllerSnapshot + Send + Sync>,
    }

    impl ControllerContext {
        pub fn new(
            environment: DeploymentEnvironment,
            snapshot: impl Fn() -> ControllerSnapshot + Send + Sync + 'static,
        ) -> Self {
            Self { environment, snapshot: Arc::new(snapshot) }
        }

        pub(crate) const fn environment(&self) -> DeploymentEnvironment {
            self.environment
        }

        pub(super) fn snapshot(&self) -> ControllerSnapshot {
            (self.snapshot)()
        }
    }

    impl fmt::Debug for ControllerContext {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter
                .debug_struct("ControllerContext")
                .finish_non_exhaustive()
        }
    }
}

#[cfg(feature = "ssr")]
pub use context::{ControllerContext, OnboardingContext};

#[cfg(not(feature = "ssr"))]
#[derive(Clone, Debug)]
pub struct OnboardingContext;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeploymentEnvironment {
    Development,
    Preview,
    Production,
}

impl DeploymentEnvironment {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Development => "Development",
            Self::Preview => "Preview",
            Self::Production => "Production",
        }
    }

    #[cfg(feature = "ssr")]
    pub(crate) const fn favicon_href(self) -> &'static str {
        match self {
            Self::Development => "/favicon-development.svg",
            Self::Preview => "/favicon-preview.svg",
            Self::Production => "/favicon.svg",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControllerComponentKind {
    Http,
    Database,
    Identity,
    Certificate,
    Enrollment,
    Zenoh,
}

impl ControllerComponentKind {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Http => "Controller",
            Self::Database => "Database",
            Self::Identity => "Identity",
            Self::Certificate => "Certificates",
            Self::Enrollment => "Enrollment",
            Self::Zenoh => "Zenoh",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ControllerComponentState {
    Ready,
    Starting,
    Degraded,
    Disabled,
}

impl ControllerComponentState {
    pub const fn label(self) -> &'static str {
        match self {
            Self::Ready => "Ready",
            Self::Starting => "Starting",
            Self::Degraded => "Degraded",
            Self::Disabled => "Disabled",
        }
    }

    pub const fn class(self) -> &'static str {
        match self {
            Self::Ready => "component-state component-state-ready",
            Self::Starting => "component-state component-state-starting",
            Self::Degraded => "component-state component-state-degraded",
            Self::Disabled => "component-state component-state-disabled",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControllerComponent {
    pub kind: ControllerComponentKind,
    pub state: ControllerComponentState,
    pub summary: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ControllerSnapshot {
    pub environment: DeploymentEnvironment,
    pub ready: bool,
    pub components: Vec<ControllerComponent>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, thiserror::Error)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ControllerError {
    #[error("Controller status is unavailable.")]
    Unavailable,
    #[error("The controller request failed.")]
    Transport,
}

impl FromServerFnError for ControllerError {
    type Encoder = JsonEncoding;

    fn from_server_fn_error(_error: ServerFnErrorErr) -> Self {
        Self::Transport
    }
}

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
    pub site_id: String,
    pub label: Option<String>,
    pub state: InvitationState,
    pub expires_at: DateTime<Utc>,
    pub agent_id: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InvitationPreview {
    pub invitation_id: String,
    pub site_id: String,
    pub expires_at: DateTime<Utc>,
    pub state: InvitationState,
    pub controller_name: Option<String>,
    pub controller_instance_id: String,
    pub supported_protocol_versions: Vec<NaiveDate>,
    pub certificate_mode: CertificateMode,
    pub requires_mutual_tls_after_issuance: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FleetOverview {
    pub site_count: usize,
    pub agent_count: usize,
    pub online_agent_count: usize,
    pub device_count: u64,
    pub sites: Vec<SiteOverview>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "availability", content = "overview", rename_all = "snake_case")]
pub enum FleetAvailability {
    Available(FleetOverview),
    Disabled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SiteOverview {
    pub site_id: String,
    pub name: String,
    pub agent_count: u64,
    pub device_count: u64,
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

    pub const fn is_revocable(self) -> bool {
        matches!(self, Self::Created | Self::Claimed)
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
pub async fn load_controller_snapshot() -> Result<ControllerSnapshot, ControllerError> {
    let context = use_context::<ControllerContext>().ok_or(ControllerError::Unavailable)?;
    Ok(context.snapshot())
}

#[server(prefix = "/_server/inari")]
pub async fn load_fleet_overview() -> Result<FleetAvailability, OnboardingError> {
    use self::ssr::*;

    let context = use_context::<OnboardingContext>()
        .ok_or_else(|| internal_error("Leptos onboarding context was not provided"))?;
    let Some(service) = context.service() else {
        return Ok(FleetAvailability::Disabled);
    };
    let _identity = require_permission(Permission::FleetRead).await?;
    let repository = service.repository();
    let sites = repository
        .sites(service.organization_id())
        .await
        .map_err(onboarding_error)?;
    let agents = repository
        .agents(service.organization_id(), None)
        .await
        .map_err(onboarding_error)?;
    let online_agent_count = agents
        .iter()
        .filter(|agent| agent.health.state == AgentHealthState::Online)
        .count();
    let device_count = sites
        .iter()
        .map(|site| site.device_count)
        .sum();
    Ok(FleetAvailability::Available(FleetOverview {
        site_count: sites.len(),
        agent_count: agents.len(),
        online_agent_count,
        device_count,
        sites: sites
            .into_iter()
            .map(|site| SiteOverview {
                site_id: site.site_id.to_string(),
                name: site.name,
                agent_count: site.agent_count,
                device_count: site.device_count,
            })
            .collect(),
    }))
}

#[server(prefix = "/_server/inari")]
pub async fn issue_invitation(label: Option<String>) -> Result<IssuedInvitation, OnboardingError> {
    use self::ssr::*;

    let service = onboarding()?;
    let identity = require_permission(Permission::EnrollmentManage).await?;
    let invitation = service
        .create_invitation(
            inari_gateway::onboarding::CreateInvitation { label },
            &audit_context(&identity),
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
pub async fn load_invitations() -> Result<Vec<InvitationStatus>, OnboardingError> {
    use self::ssr::*;

    let service = onboarding()?;
    let _identity = require_permission(Permission::EnrollmentManage).await?;
    service
        .invitations()
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
pub async fn revoke_invitation(invitation_id: String) -> Result<InvitationStatus, OnboardingError> {
    use self::ssr::*;

    let service = onboarding()?;
    let identity = require_permission(Permission::EnrollmentManage).await?;
    let invitation_id = invitation_id
        .parse()
        .map_err(onboarding_error)?;
    service
        .revoke_invitation(&invitation_id, &audit_context(&identity))
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
#[path = "server_fns/ssr.rs"]
mod ssr;
