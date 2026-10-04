use chrono::{DateTime, Utc};
use secrecy::SecretString;
#[cfg(any(windows, test))]
use serde::Deserialize;
#[cfg(any(windows, test))]
use url::Url;

#[cfg(any(windows, test))]
use crate::{AgentClientError, AgentClientResult};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[cfg_attr(not(windows), derive(Default))]
pub enum PairingMode {
    Native,
    #[cfg_attr(not(windows), default)]
    Loopback,
}

#[cfg(windows)]
impl Default for PairingMode {
    fn default() -> Self {
        let mut length = 0;
        // SAFETY: length is writable and the optional buffer is null with zero capacity.
        let status = unsafe {
            windows_sys::Win32::Storage::Packaging::Appx::GetCurrentPackageFamilyName(
                &mut length,
                std::ptr::null_mut(),
            )
        };
        if status == windows_sys::Win32::Foundation::APPMODEL_ERROR_NO_PACKAGE {
            Self::Loopback
        } else {
            // An uncertain package lookup must retain the native authentication boundary.
            Self::Native
        }
    }
}

pub(crate) struct PairingGrant {
    pub secret: SecretString,
    pub expires_at: DateTime<Utc>,
}

#[cfg(windows)]
pub(crate) async fn native_pairing_grant() -> AgentClientResult<PairingGrant> {
    decode_pairing_grant(&crate::native_pipe::request(1).await?)
}

#[cfg(windows)]
pub(crate) async fn native_agent_endpoint() -> AgentClientResult<Url> {
    decode_agent_endpoint(&crate::native_pipe::request(2).await?)
}

#[cfg(windows)]
pub(crate) async fn native_setup_restart() -> AgentClientResult<()> {
    decode_setup_restart(&crate::native_pipe::request(3).await?)
}

#[cfg(any(windows, test))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeSetupRestartResponse {
    restart_requested: bool,
}

#[cfg(any(windows, test))]
fn decode_setup_restart(payload: &str) -> AgentClientResult<()> {
    let response: NativeSetupRestartResponse =
        serde_json::from_str(payload).map_err(AgentClientError::invalid_response)?;
    if !response.restart_requested {
        return Err(AgentClientError::Rejected);
    }
    Ok(())
}

#[cfg(windows)]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativePairingGrant {
    pairing_secret: String,
    expires_at: DateTime<Utc>,
}

#[cfg(windows)]
fn decode_pairing_grant(payload: &str) -> AgentClientResult<PairingGrant> {
    let response: NativePairingGrant =
        serde_json::from_str(payload).map_err(AgentClientError::invalid_response)?;
    if response.pairing_secret.is_empty() || response.expires_at <= Utc::now() {
        return Err(AgentClientError::MalformedIdentity);
    }
    Ok(PairingGrant {
        secret: SecretString::from(response.pairing_secret),
        expires_at: response.expires_at,
    })
}

#[cfg(any(windows, test))]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeEndpointResponse {
    agent_endpoint: Url,
}

#[cfg(any(windows, test))]
fn decode_agent_endpoint(payload: &str) -> AgentClientResult<Url> {
    let response: NativeEndpointResponse =
        serde_json::from_str(payload).map_err(AgentClientError::invalid_response)?;
    let endpoint = response.agent_endpoint;
    let loopback = match endpoint.host() {
        Some(url::Host::Domain("localhost")) => true,
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        _ => false,
    };
    if endpoint.host_str().is_none()
        || !(endpoint.scheme() == "https" || (endpoint.scheme() == "http" && loopback))
        || !endpoint.username().is_empty()
        || endpoint.password().is_some()
        || endpoint.path() != "/"
        || endpoint.query().is_some()
        || endpoint.fragment().is_some()
    {
        return Err(AgentClientError::invalid_response(std::io::Error::other(
            "The native Agent Endpoint must use HTTPS or local loopback HTTP without credentials, a path, query, or fragment.",
        )));
    }
    Ok(endpoint)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn setup_restart_requires_an_explicit_acknowledgment() {
        assert!(decode_setup_restart(r#"{"restart_requested":true}"#).is_ok());
        for payload in [
            r#"{"restart_requested":false}"#,
            r#"{}"#,
            r#"{"restart_requested":true,"service_state":"running"}"#,
        ] {
            assert!(decode_setup_restart(payload).is_err(), "{payload}");
        }
    }

    #[test]
    fn accepts_the_configured_https_agent_endpoint() {
        let endpoint =
            decode_agent_endpoint(r#"{"agent_endpoint":"https://agent.example.com:7310/"}"#)
                .expect("valid fixture");
        assert_eq!(endpoint.as_str(), "https://agent.example.com:7310/");
    }

    #[test]
    fn rejects_insecure_or_ambiguous_native_endpoints() {
        for endpoint in [
            "http://agent.example.com:7310/",
            "https://user:secret@agent.example.com:7310/",
            "https://agent.example.com:7310/api",
            "https://agent.example.com:7310/?secret=value",
            "https://agent.example.com:7310/#fragment",
            "file:///private/config",
        ] {
            let payload = serde_json::json!({"agent_endpoint": endpoint}).to_string();
            assert!(decode_agent_endpoint(&payload).is_err(), "{endpoint}");
        }
    }

    #[test]
    fn loopback_discovery_keeps_the_service_port() {
        let endpoint = decode_agent_endpoint(r#"{"agent_endpoint":"http://127.0.0.1:7410/"}"#)
            .expect("valid fixture");
        assert_eq!(endpoint.port(), Some(7410));
    }

    #[test]
    fn accepts_literal_loopback_addresses_without_tls() {
        for endpoint in ["http://[::1]:7410/", "http://127.0.0.2:7410/"] {
            let payload = serde_json::json!({"agent_endpoint": endpoint}).to_string();
            assert_eq!(
                decode_agent_endpoint(&payload)
                    .unwrap()
                    .as_str(),
                endpoint
            );
        }
    }

    #[test]
    fn rejects_non_loopback_addresses_without_tls() {
        for endpoint in ["http://[::]:7410/", "http://192.168.1.1:7410/"] {
            let payload = serde_json::json!({"agent_endpoint": endpoint}).to_string();
            assert!(decode_agent_endpoint(&payload).is_err(), "{endpoint}");
        }
    }
}
