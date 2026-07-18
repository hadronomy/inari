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

    let mut payload = String::new();
    pipe.take(RESPONSE_LIMIT)
        .read_to_string(&mut payload)
        .map_err(AgentClientError::pairing_unavailable)?;
    decode_pairing_grant(&payload)
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
