use chrono::{DateTime, Utc};
use url::Url;

use crate::{AgentClientError, AgentClientResult, PairingRequestId, transport};

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
#[error("Enter a Pairing Request ID or an inari://pairing/ link.")]
pub struct PairingLinkError;

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum InariLink {
    Enrollment(crate::InvitationLink),
    ClientPairing(PairingRequestId),
}

impl InariLink {
    pub fn parse(value: &str) -> Result<Self, InariLinkError> {
        let value = value.trim();
        let url = Url::parse(value).map_err(|_| InariLinkError)?;
        if url.host_str() == Some("pairing") {
            PairingRequestId::parse_input(value)
                .map(Self::ClientPairing)
                .map_err(|_| InariLinkError)
        } else {
            crate::InvitationLink::parse(value)
                .map(Self::Enrollment)
                .map_err(|_| InariLinkError)
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
#[error("This is not a valid Inari enrollment or Client Pairing link.")]
pub struct InariLinkError;

impl PairingRequestId {
    pub fn parse_input(value: &str) -> Result<Self, PairingLinkError> {
        let value = value.trim();
        if !value.contains("://") {
            return Self::parse(value).map_err(|_| PairingLinkError);
        }
        let url = Url::parse(value).map_err(|_| PairingLinkError)?;
        if url.scheme() != "inari"
            || url.host_str() != Some("pairing")
            || !url.username().is_empty()
            || url.password().is_some()
            || url.port().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(PairingLinkError);
        }
        let id = Self::parse(
            url.path()
                .strip_prefix('/')
                .ok_or(PairingLinkError)?,
        )
        .map_err(|_| PairingLinkError)?;
        if value != format!("inari://pairing/{id}") {
            return Err(PairingLinkError);
        }
        Ok(id)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PairingDecision {
    Approve,
    Deny,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PairingRequestState {
    Pending,
    Approved,
    Denied,
    Canceled,
    Expired,
    Completed,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PairingScope {
    pub agent_id: String,
    pub agent_endpoint: Url,
    pub browser_origin: Url,
    pub database: String,
    pub company_id: String,
    pub organization_id: String,
    pub site_id: String,
    pub pos_configuration_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PairingRequest {
    pub id: PairingRequestId,
    pub scope: PairingScope,
    pub phrase: String,
    pub requested_permissions: Vec<String>,
    pub expires_at: DateTime<Utc>,
    pub state: PairingRequestState,
}

impl PairingRequest {
    pub fn can_decide_at(&self, at: DateTime<Utc>) -> bool {
        self.state == PairingRequestState::Pending && at < self.expires_at
    }
}

impl TryFrom<transport::types::PairingRequestResponse> for PairingRequest {
    type Error = AgentClientError;

    fn try_from(value: transport::types::PairingRequestResponse) -> AgentClientResult<Self> {
        let state = match value.state.as_str() {
            "pending" => PairingRequestState::Pending,
            "approved" => PairingRequestState::Approved,
            "denied" => PairingRequestState::Denied,
            "canceled" => PairingRequestState::Canceled,
            "expired" => PairingRequestState::Expired,
            "completed" => PairingRequestState::Completed,
            _ => {
                return Err(AgentClientError::invalid_response(std::io::Error::other(
                    "Unknown Pairing Request state.",
                )));
            },
        };
        Ok(Self {
            id: PairingRequestId::parse(value.request_id)
                .map_err(AgentClientError::invalid_response)?,
            scope: PairingScope {
                agent_id: value.scope.agent_id,
                agent_endpoint: Url::parse(&value.scope.agent_endpoint)
                    .map_err(AgentClientError::invalid_response)?,
                browser_origin: Url::parse(&value.scope.browser_origin)
                    .map_err(AgentClientError::invalid_response)?,
                database: value.scope.database,
                company_id: value.scope.company_id,
                organization_id: value.scope.organization_id,
                site_id: value.scope.site_id,
                pos_configuration_id: value.scope.pos_configuration_id,
            },
            phrase: value.phrase,
            requested_permissions: value.requested_permissions,
            expires_at: value.expires_at,
            state,
        })
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    #[test]
    fn pairing_links_accept_only_one_request_on_the_pairing_authority() {
        let id = PairingRequestId::parse_input("inari://pairing/req_123").unwrap();
        assert_eq!(id.as_str(), "req_123");
        assert_eq!(PairingRequestId::parse_input(" req_123 ").unwrap(), id);
        for value in [
            "inari://pairing/",
            "inari://pairing/req_123/extra",
            "inari://pairing/req_123?approve=true",
            "inari://pairing/req_123#secret",
            "inari://user@pairing/req_123",
            "inari://pairing:7310/req_123",
            "https://pairing/req_123",
            "inari://setup/req_123",
            "inari://pairing/%72eq_123",
            "inari://pairing/req_123/../req_other",
            "inari://pairing/./req_123",
            "inari://pairing/req_1\t23",
        ] {
            assert!(PairingRequestId::parse_input(value).is_err(), "{value}");
        }
    }

    #[test]
    fn activation_distinguishes_client_pairing_from_enrollment() {
        assert!(matches!(
            InariLink::parse("inari://pairing/req_123").unwrap(),
            InariLink::ClientPairing(_)
        ));
        assert!(matches!(
            InariLink::parse("inari://setup/controller.example.com#invitation").unwrap(),
            InariLink::Enrollment(_)
        ));
        assert!(InariLink::parse("inari://pairing/req_123#invitation").is_err());
    }

    pub(crate) fn response(state: &str) -> transport::types::PairingRequestResponse {
        serde_json::from_value(serde_json::json!({
            "request_id": "req_123",
            "scope": {
                "agent_id": "agent_1", "agent_endpoint": "https://agent.example.com:7310",
                "browser_origin": "https://odoo.example.com", "database": "odoo",
                "company_id": "1", "organization_id": "org_1", "site_id": "site_1",
                "pos_configuration_id": "1", "audience": "inari-agent",
            },
            "browser_jwk_thumbprint": "browser-thumbprint",
            "requested_permissions": ["device_work:receipt_image"],
            "session_nonce": "session-nonce", "phrase": "maple river cloud stone",
            "approval_uri": "inari://pairing/req_123",
            "created_at": "2026-10-03T10:00:00Z", "expires_at": "2026-10-03T10:05:00Z",
            "state": state,
        }))
        .unwrap()
    }

    #[test]
    fn transport_projection_preserves_the_scope_and_rejects_unknown_states() {
        let request = PairingRequest::try_from(response("pending")).unwrap();
        assert_eq!(request.id.as_str(), "req_123");
        assert_eq!(request.scope.browser_origin.as_str(), "https://odoo.example.com/");
        assert_eq!(request.scope.agent_endpoint.port(), Some(7310));
        assert_eq!(
            request
                .scope
                .pos_configuration_id
                .as_deref(),
            Some("1")
        );
        assert_eq!(request.requested_permissions, ["device_work:receipt_image"]);
        for state in ["approved", "denied", "canceled", "expired", "completed"] {
            let request = PairingRequest::try_from(response(state)).unwrap();
            assert!(!request.can_decide_at(request.expires_at - chrono::Duration::seconds(1)));
        }
        assert!(PairingRequest::try_from(response("unexpected")).is_err());
    }
}
