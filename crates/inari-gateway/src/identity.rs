use std::collections::BTreeSet;
use std::fmt;
use std::str::FromStr;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::protocol::AgentId;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct ActorId(String);

impl ActorId {
    #[must_use]
    pub fn from_oidc_subject(subject: &str) -> Self {
        Self(format!("oidc:{subject}"))
    }

    #[must_use]
    pub fn from_agent(agent_id: &AgentId) -> Self {
        Self(format!("agent:{agent_id}"))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl FromStr for ActorId {
    type Err = crate::GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        if value.is_empty() || value.len() > 512 || value.chars().any(char::is_control) {
            return Err(crate::GatewayError::InvalidInput(
                "actor IDs must be non-empty, contain no control characters, and be at most 512 bytes"
                    .into(),
            ));
        }
        Ok(Self(value.to_owned()))
    }
}

impl TryFrom<String> for ActorId {
    type Error = crate::GatewayError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl fmt::Display for ActorId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AccessRole {
    Viewer,
    Operator,
    EnrollmentAdmin,
    Administrator,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Permission {
    FleetRead,
    JobsWrite,
    EnrollmentManage,
    AuditRead,
    ZenohRead,
    ZenohWrite,
    Administration,
}

impl AccessRole {
    #[must_use]
    pub const fn grants(self, permission: Permission) -> bool {
        match self {
            Self::Administrator => true,
            Self::Viewer => matches!(permission, Permission::FleetRead),
            Self::Operator => {
                matches!(permission, Permission::FleetRead | Permission::JobsWrite)
            },
            Self::EnrollmentAdmin => {
                matches!(permission, Permission::FleetRead | Permission::EnrollmentManage)
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SessionIdentity {
    pub actor_id: ActorId,
    pub display_name: Option<String>,
    pub email: Option<String>,
    pub roles: BTreeSet<AccessRole>,
    pub expires_at: DateTime<Utc>,
}

impl SessionIdentity {
    #[must_use]
    pub fn grants(&self, permission: Permission) -> bool {
        self.roles
            .iter()
            .any(|role| role.grants(permission))
    }
}
