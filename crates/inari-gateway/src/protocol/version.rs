use std::fmt;
use std::str::FromStr;

use chrono::NaiveDate;
use serde::{Deserialize, Serialize};

pub const GATEWAY_PROTOCOL_VERSION: &str = "2026-07-12";
const AGENT_ID_PREFIX: &str = "agt_";
const MAX_AGENT_ID_LENGTH: usize = 64;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct AgentId(String);

impl AgentId {
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for AgentId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for AgentId {
    type Err = crate::GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let suffix = value
            .strip_prefix(AGENT_ID_PREFIX)
            .filter(|suffix| !suffix.is_empty());
        let valid = value.len() <= MAX_AGENT_ID_LENGTH
            && suffix.is_some_and(|suffix| {
                suffix
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
            });
        if !valid {
            return Err(crate::GatewayError::InvalidInput(
                "agent IDs must start with `agt_`, contain only lowercase ASCII letters, digits, or underscores, and be at most 64 characters"
                    .into(),
            ));
        }
        Ok(Self(value.into()))
    }
}

impl TryFrom<String> for AgentId {
    type Error = crate::GatewayError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl From<AgentId> for String {
    fn from(agent_id: AgentId) -> Self {
        agent_id.0
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct ProtocolVersion(String);

impl ProtocolVersion {
    #[must_use]
    pub fn current() -> Self {
        Self(GATEWAY_PROTOCOL_VERSION.into())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ProtocolVersion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.as_str())
    }
}

impl FromStr for ProtocolVersion {
    type Err = crate::GatewayError;

    fn from_str(value: &str) -> Result<Self, Self::Err> {
        let valid_shape = value.len() == 10
            && value
                .bytes()
                .enumerate()
                .all(|(index, byte)| {
                    matches!(index, 4 | 7) && byte == b'-'
                        || !matches!(index, 4 | 7) && byte.is_ascii_digit()
                });
        if !valid_shape || NaiveDate::parse_from_str(value, "%Y-%m-%d").is_err() {
            return Err(crate::GatewayError::InvalidInput(
                "protocol versions must be valid ISO 8601 calendar dates".into(),
            ));
        }
        Ok(Self(value.into()))
    }
}

impl TryFrom<String> for ProtocolVersion {
    type Error = crate::GatewayError;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        value.parse()
    }
}

impl From<ProtocolVersion> for String {
    fn from(version: ProtocolVersion) -> Self {
        version.0
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProtocolDescriptor {
    pub version: ProtocolVersion,
    pub supported_versions: Vec<ProtocolVersion>,
}

impl Default for ProtocolDescriptor {
    fn default() -> Self {
        Self {
            version: ProtocolVersion::current(),
            supported_versions: vec![ProtocolVersion::current()],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{AgentId, ProtocolVersion};

    #[test]
    fn agent_id_accepts_a_single_safe_key_expression_segment() {
        let agent_id = "agt_browser_audit"
            .parse::<AgentId>()
            .expect("agent ID should parse");

        assert_eq!(agent_id.as_str(), "agt_browser_audit");
        assert_eq!(serde_json::to_string(&agent_id).unwrap(), "\"agt_browser_audit\"");
    }

    #[test]
    fn agent_id_rejects_path_syntax_and_unicode() {
        for value in ["browser_audit", "agt_", "agt_browser/audit", "agt_*", "agt_浏览器"] {
            assert!(value.parse::<AgentId>().is_err(), "accepted {value:?}");
        }
    }

    #[test]
    fn protocol_version_round_trips_as_an_iso_date() {
        let version = ProtocolVersion::current();
        let json = serde_json::to_string(&version).expect("protocol version should serialize");

        assert_eq!(json, "\"2026-07-12\"");
        assert_eq!(
            serde_json::from_str::<ProtocolVersion>(&json)
                .expect("protocol version should deserialize"),
            version,
        );
    }

    #[test]
    fn protocol_version_rejects_invalid_dates_and_shapes() {
        for value in ["2026-02-30", "2026-7-11", "latest", "２０２６-０７-１１"] {
            assert!(
                value
                    .parse::<ProtocolVersion>()
                    .is_err(),
                "accepted {value:?}"
            );
        }
    }
}
