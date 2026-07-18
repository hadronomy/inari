use std::{fmt, str::FromStr};

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use url::Url;

use crate::{AgentClientError, AgentClientResult, transport};

macro_rules! identifier {
    ($name:ident, $label:literal) => {
        #[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd, Serialize, Deserialize)]
        #[serde(transparent)]
        pub struct $name(String);

        impl $name {
            pub fn parse(value: impl Into<String>) -> Result<Self, IdentifierError> {
                let value = value.into();
                let valid = !value.is_empty()
                    && value.len() <= 128
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'));
                valid
                    .then_some(Self(value))
                    .ok_or(IdentifierError { kind: $label })
            }

            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

identifier!(DeviceId, "device");
identifier!(JobId, "job");

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
#[error("invalid {kind} identifier")]
pub struct IdentifierError {
    kind: &'static str,
}

#[derive(Clone, Eq, PartialEq)]
pub struct InvitationLink(Url);

impl InvitationLink {
    pub fn parse(value: &str) -> Result<Self, InvitationLinkError> {
        let url = Url::parse(value).map_err(|_| InvitationLinkError)?;
        (url.scheme() == "inari"
            && url
                .fragment()
                .is_some_and(|fragment| !fragment.is_empty()))
        .then_some(Self(url))
        .ok_or(InvitationLinkError)
    }

    pub(crate) fn transport_value(&self) -> AgentClientResult<transport::types::Invitation> {
        transport::types::Invitation::try_from(self.0.as_str().to_owned())
            .map_err(AgentClientError::invalid_response)
    }
}

impl fmt::Debug for InvitationLink {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("InvitationLink([REDACTED])")
    }
}

impl FromStr for InvitationLink {
    type Err = InvitationLinkError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        Self::parse(value)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
#[error("invalid Inari invitation link")]
pub struct InvitationLinkError;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SetupAccess {
    Unknown,
    Required,
    Complete,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SetupStage {
    Invitation,
    Securing,
    Connecting,
    Devices,
    Failed,
    Complete,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SetupSnapshot {
    pub access: SetupAccess,
    pub stage: SetupStage,
    pub completed_at: Option<DateTime<Utc>>,
    pub guidance: Option<String>,
    pub devices: Vec<Device>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EnrollmentPreview {
    pub controller_name: Option<String>,
    pub controller_url: Url,
    pub expires_at: DateTime<Utc>,
    pub requires_mutual_tls: bool,
    pub supported_protocol_versions: Vec<String>,
}

impl SetupSnapshot {
    pub fn invitation() -> Self {
        Self {
            access: SetupAccess::Required,
            stage: SetupStage::Invitation,
            completed_at: None,
            guidance: None,
            devices: Vec::new(),
        }
    }

    pub fn unavailable() -> Self {
        Self::unavailable_with(
            "Device Center could not reach the local agent. Start the service, then try again.",
        )
    }

    pub fn unavailable_with(guidance: impl Into<String>) -> Self {
        Self {
            access: SetupAccess::Unknown,
            stage: SetupStage::Invitation,
            completed_at: None,
            guidance: Some(guidance.into()),
            devices: Vec::new(),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AgentConnection {
    Checking,
    Connected,
    Reconnecting,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServiceState {
    Checking,
    Starting,
    Running,
    Stopped,
    NotInstalled,
    Unavailable,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeviceState {
    Online,
    Offline,
    Degraded,
    Blocked,
    Unknown,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DeviceKind {
    Printer,
    Scale,
    Scanner,
    Other,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Device {
    pub id: DeviceId,
    pub name: String,
    pub kind: DeviceKind,
    pub state: DeviceState,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JobState {
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Job {
    pub id: JobId,
    pub device_id: DeviceId,
    pub state: JobState,
    pub created_at: DateTime<Utc>,
}

impl TryFrom<transport::types::ManagedOnboardingStatusResponse> for SetupSnapshot {
    type Error = AgentClientError;

    fn try_from(
        response: transport::types::ManagedOnboardingStatusResponse,
    ) -> Result<Self, Self::Error> {
        use transport::types::Phase;

        let access = if response.completed_at.is_some() {
            SetupAccess::Complete
        } else {
            SetupAccess::Required
        };
        let stage = match response.phase {
            Phase::NotStarted => SetupStage::Invitation,
            Phase::RestartRequired | Phase::SecuringConnection => SetupStage::Securing,
            Phase::Connecting => SetupStage::Connecting,
            Phase::FindingDevices => SetupStage::Devices,
            Phase::Ready if access == SetupAccess::Complete => SetupStage::Complete,
            Phase::Ready => SetupStage::Devices,
            Phase::Failed => SetupStage::Failed,
        };
        let guidance = response
            .last_error
            .or_else(|| (!response.detail.trim().is_empty()).then_some(response.detail));
        let devices = response
            .devices
            .into_iter()
            .map(map_device)
            .collect::<AgentClientResult<Vec<_>>>()?;
        Ok(Self { access, stage, completed_at: response.completed_at, guidance, devices })
    }
}

impl TryFrom<transport::types::ManagedOnboardingPreviewResponse> for EnrollmentPreview {
    type Error = AgentClientError;

    fn try_from(
        response: transport::types::ManagedOnboardingPreviewResponse,
    ) -> Result<Self, Self::Error> {
        let controller_url =
            Url::parse(&response.controller_url).map_err(AgentClientError::invalid_response)?;
        Ok(Self {
            controller_name: response.controller_name,
            controller_url,
            expires_at: response.expires_at,
            requires_mutual_tls: response.requires_mutual_tls_after_issuance,
            supported_protocol_versions: response.supported_protocol_versions,
        })
    }
}

pub(crate) fn map_devices(
    response: transport::types::DeviceDirectoryResponse,
) -> AgentClientResult<Vec<Device>> {
    response
        .devices
        .into_iter()
        .map(map_device)
        .collect()
}

fn map_device(device: transport::types::DeviceResponse) -> AgentClientResult<Device> {
    let kind = match device.kind {
        transport::types::DeviceKind::Printer => DeviceKind::Printer,
        transport::types::DeviceKind::Scale => DeviceKind::Scale,
        transport::types::DeviceKind::Scanner => DeviceKind::Scanner,
        transport::types::DeviceKind::Display => DeviceKind::Other,
    };
    let state = match device.connection.state {
        transport::types::DeviceConnectionState::Online => DeviceState::Online,
        transport::types::DeviceConnectionState::Offline => DeviceState::Offline,
    };
    Ok(Device {
        id: DeviceId::parse(device.id).map_err(AgentClientError::invalid_response)?,
        name: device.name,
        kind,
        state,
    })
}

pub(crate) fn map_jobs(
    response: transport::types::JobCollectionResponse,
) -> AgentClientResult<Vec<Job>> {
    response
        .jobs
        .into_iter()
        .map(|job| {
            let state = match job.state {
                transport::types::JobState::Queued => JobState::Queued,
                transport::types::JobState::Dispatched | transport::types::JobState::Running => {
                    JobState::Running
                },
                transport::types::JobState::RetryScheduled => JobState::Queued,
                transport::types::JobState::Succeeded => JobState::Succeeded,
                transport::types::JobState::Failed => JobState::Failed,
                transport::types::JobState::Cancelled => JobState::Cancelled,
            };
            Ok(Job {
                id: JobId::parse(job.id).map_err(AgentClientError::invalid_response)?,
                device_id: DeviceId::parse(job.target.device_id)
                    .map_err(AgentClientError::invalid_response)?,
                state,
                created_at: job.created_at,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_path_shaped_identifiers() {
        assert!(DeviceId::parse("../printer").is_err());
        assert!(DeviceId::parse("front desk").is_err());
    }

    #[test]
    fn invitation_requires_fragment_secret() {
        assert!(InvitationLink::parse("inari://enroll?invite=inv_01#code=secret").is_ok());
        assert!(InvitationLink::parse("inari://enroll?invite=inv_01").is_err());
        assert!(InvitationLink::parse("https://inari.example/enroll#code=secret").is_err());
    }

    #[test]
    fn invitation_debug_output_never_contains_the_secret() {
        let invitation =
            InvitationLink::parse("inari://enroll?invite=inv_01#code=very-secret").unwrap();

        assert_eq!(format!("{invitation:?}"), "InvitationLink([REDACTED])");
    }

    #[test]
    fn completion_checkpoint_is_the_only_setup_unlock() {
        let incomplete: transport::types::ManagedOnboardingStatusResponse =
            serde_json::from_value(serde_json::json!({
                "phase": "ready",
                "detail": "Choose devices",
                "devices": [],
                "completed_at": null
            }))
            .expect("fixture parses");
        let complete: transport::types::ManagedOnboardingStatusResponse =
            serde_json::from_value(serde_json::json!({
                "phase": "ready",
                "detail": "Ready",
                "devices": [],
                "completed_at": "2026-07-17T10:00:00Z"
            }))
            .expect("fixture parses");

        assert_eq!(
            SetupSnapshot::try_from(incomplete)
                .unwrap()
                .access,
            SetupAccess::Required
        );
        assert_eq!(
            SetupSnapshot::try_from(complete)
                .unwrap()
                .access,
            SetupAccess::Complete
        );
    }
}
