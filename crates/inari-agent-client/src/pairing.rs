use chrono::{DateTime, Utc};
use secrecy::SecretString;
#[cfg(windows)]
use serde::Deserialize;

#[cfg(windows)]
use crate::{AgentClientError, AgentClientResult};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PairingMode {
    Native,
    Loopback,
}

impl Default for PairingMode {
    fn default() -> Self {
        if cfg!(windows) { Self::Native } else { Self::Loopback }
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
    use std::{
        fs::OpenOptions,
        io::{Read as _, Write as _},
        thread,
        time::{Duration, Instant},
    };

    const PIPE: &str = r"\\.\pipe\Inari.Agent.Pairing";
    const REQUEST: [u8; 1] = [1];
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
    pipe.write_all(&REQUEST)
        .and_then(|()| pipe.flush())
        .map_err(AgentClientError::pairing_unavailable)?;

    // Read the reply message rather than draining to end of stream. The server
    // disconnects as soon as it has flushed, and a disconnect turns a still
    // pending read into ERROR_PIPE_NOT_CONNECTED instead of a clean end. Once
    // the reply is in hand that disconnect is the expected close, so only an
    // empty payload counts as having lost the answer.
    let mut payload = Vec::new();
    let mut chunk = [0u8; 512];
    loop {
        match pipe.read(&mut chunk) {
            Ok(0) => break,
            Ok(read) => {
                payload.extend_from_slice(&chunk[..read]);
                if payload.len() as u64 > RESPONSE_LIMIT {
                    return Err(AgentClientError::MalformedIdentity);
                }
            },
            Err(error) if is_peer_closed(&error) && !payload.is_empty() => break,
            Err(error) => return Err(AgentClientError::pairing_unavailable(error)),
        }
    }
    let payload = String::from_utf8(payload).map_err(AgentClientError::invalid_response)?;
    decode_pairing_grant(&payload)
}

/// Whether the server hung up, by either of the two codes Windows uses.
#[cfg(windows)]
fn is_peer_closed(error: &std::io::Error) -> bool {
    const ERROR_BROKEN_PIPE: i32 = 109;
    const ERROR_PIPE_NOT_CONNECTED: i32 = 233;
    matches!(error.raw_os_error(), Some(ERROR_BROKEN_PIPE | ERROR_PIPE_NOT_CONNECTED))
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
