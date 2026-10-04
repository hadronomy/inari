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
    tokio::task::spawn_blocking(read_native_pairing_grant)
        .await
        .map_err(AgentClientError::pairing_unavailable)?
}

#[cfg(windows)]
fn read_native_pairing_grant() -> AgentClientResult<PairingGrant> {
    decode_pairing_grant(&read_native_response([1])?)
}

#[cfg(windows)]
pub(crate) async fn native_agent_endpoint() -> AgentClientResult<Url> {
    tokio::task::spawn_blocking(|| decode_agent_endpoint(&read_native_response([2])?))
        .await
        .map_err(AgentClientError::pairing_unavailable)?
}

#[cfg(windows)]
fn read_native_response(request: [u8; 1]) -> AgentClientResult<String> {
    use std::{
        fs::OpenOptions,
        io::{Read as _, Write as _},
        thread,
        time::{Duration, Instant},
    };

    const PIPE: &str = r"\\.\pipe\Inari.Agent.Pairing";
    const RESPONSE_LIMIT: u64 = 4_096;
    const CONNECT_TIMEOUT: Duration = Duration::from_secs(2);

    let started = Instant::now();
    let mut pipe = loop {
        match OpenOptions::new()
            .read(true)
            .write(true)
            .open(PIPE)
        {
            Ok(pipe) => break pipe,
            Err(error) if started.elapsed() < CONNECT_TIMEOUT => {
                thread::sleep(Duration::from_millis(40));
                drop(error);
            },
            Err(error) => return Err(AgentClientError::pairing_unavailable(error)),
        }
    };
    pipe.write_all(&request)
        .and_then(|()| pipe.flush())
        .map_err(AgentClientError::pairing_unavailable)?;

    let mut payload = String::new();
    pipe.take(RESPONSE_LIMIT)
        .read_to_string(&mut payload)
        .map_err(AgentClientError::pairing_unavailable)?;
    Ok(payload)
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
