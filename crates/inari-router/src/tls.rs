use std::io::{self, BufReader};
use std::path::PathBuf;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use chrono::{DateTime, Utc};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use x509_parser::prelude::FromDer;
use zeroize::Zeroizing;

use crate::{RouterConfig, RouterError, RouterResult};

#[derive(Clone, Debug)]
pub(crate) struct TlsFiles {
    pub(crate) root_ca: PathBuf,
    pub(crate) certificate: PathBuf,
    pub(crate) private_key: PathBuf,
}

pub(crate) struct TlsMaterial {
    pub(crate) root_ca: Vec<u8>,
    pub(crate) certificate: Vec<u8>,
    pub(crate) private_key: Zeroizing<Vec<u8>>,
    pub(crate) digest: [u8; 32],
    not_before: DateTime<Utc>,
    pub(crate) expires_at: DateTime<Utc>,
}

impl TlsMaterial {
    pub(crate) async fn load(files: &TlsFiles) -> RouterResult<Self> {
        let root_ca = tokio::fs::read(&files.root_ca).await?;
        let certificate = tokio::fs::read(&files.certificate).await?;
        let private_key = Zeroizing::new(tokio::fs::read(&files.private_key).await?);
        let mut digest = Sha256::new();
        for bytes in [&root_ca[..], &certificate[..], &private_key[..]] {
            if bytes.is_empty() || bytes.len() > 256 * 1024 {
                return Err(RouterError::NotReady(
                    "TLS material is empty or exceeds 256 KiB.".into(),
                ));
            }
            digest.update((bytes.len() as u64).to_be_bytes());
            digest.update(bytes);
        }
        let chain = rustls_pemfile::certs(&mut BufReader::new(certificate.as_slice()))
            .collect::<Result<Vec<_>, _>>()?;
        let mut not_before = DateTime::<Utc>::MIN_UTC;
        let mut expires_at = DateTime::<Utc>::MAX_UTC;
        if chain.is_empty() {
            return Err(RouterError::NotReady("TLS certificate chain is empty.".into()));
        }
        for certificate in &chain {
            let (remaining, parsed) =
                x509_parser::certificate::X509Certificate::from_der(certificate)
                    .map_err(io::Error::other)?;
            if !remaining.is_empty() {
                return Err(RouterError::NotReady(
                    "TLS certificate contains trailing data.".into(),
                ));
            }
            let start = DateTime::from_timestamp(parsed.validity().not_before.timestamp(), 0)
                .ok_or_else(|| io::Error::other("TLS certificate start time is invalid"))?;
            let end = DateTime::from_timestamp(parsed.validity().not_after.timestamp(), 0)
                .ok_or_else(|| io::Error::other("TLS certificate expiry is invalid"))?;
            not_before = not_before.max(start);
            expires_at = expires_at.min(end);
        }
        let material = Self {
            root_ca,
            certificate,
            private_key,
            digest: digest.finalize().into(),
            not_before,
            expires_at,
        };
        material.require_current(Utc::now())?;
        Ok(material)
    }

    pub(crate) fn require_current(&self, now: DateTime<Utc>) -> RouterResult<()> {
        if self.not_before > now || self.expires_at <= now {
            return Err(RouterError::NotReady("TLS certificate chain is not current.".into()));
        }
        Ok(())
    }
}

pub(crate) struct DataTls {
    router: TlsMaterial,
    probe: TlsMaterial,
}

impl DataTls {
    pub(crate) async fn load(config: &RouterConfig) -> RouterResult<Self> {
        let router = TlsMaterial::load(&TlsFiles {
            root_ca: config.root_ca_file.clone(),
            certificate: config.certificate_file.clone(),
            private_key: config.private_key_file.clone(),
        })
        .await?;
        let probe = TlsMaterial::load(&TlsFiles {
            root_ca: config.root_ca_file.clone(),
            certificate: config.probe_certificate_file.clone(),
            private_key: config.probe_private_key_file.clone(),
        })
        .await?;
        if router.root_ca != probe.root_ca {
            return Err(RouterError::NotReady(
                "TLS trust changed while credentials were read.".into(),
            ));
        }
        Ok(Self { router, probe })
    }

    pub(crate) fn matches(&self, other: &Self) -> bool {
        self.router.digest == other.router.digest && self.probe.digest == other.probe.digest
    }

    pub(crate) fn require_current(&self, now: DateTime<Utc>) -> RouterResult<()> {
        self.router.require_current(now)?;
        self.probe.require_current(now)
    }

    pub(crate) fn expires_at(&self) -> DateTime<Utc> {
        self.router
            .expires_at
            .min(self.probe.expires_at)
    }

    pub(crate) fn configure(&self, config: &mut Value) -> RouterResult<()> {
        // Inline credentials bind zenohd to the exact material the Supervisor monitors.
        config["transport"]["link"]["tls"] = json!({
            "root_ca_certificate_base64": STANDARD.encode(&self.router.root_ca),
            "listen_certificate_base64": STANDARD.encode(&self.router.certificate),
            "listen_private_key_base64": STANDARD.encode(&*self.router.private_key),
            "connect_certificate_base64": STANDARD.encode(&self.router.certificate),
            "connect_private_key_base64": STANDARD.encode(&*self.router.private_key),
            "enable_mtls": true,
            "verify_name_on_connect": true,
            "close_link_on_expiration": true,
        });
        zenoh::Config::from_json5(&serde_json::to_string(config)?)
            .map_err(|error| RouterError::NotReady(error.to_string()))?;
        Ok(())
    }

    pub(crate) fn probe_config(&self, endpoint: &str) -> RouterResult<zenoh::Config> {
        let config = json!({
            "mode": "client",
            "connect": {"endpoints": [endpoint], "timeout_ms": 2000},
            "listen": {"endpoints": []},
            "scouting": {"multicast": {"enabled": false}, "gossip": {"enabled": false}},
            "transport": {"link": {"tls": {
                "root_ca_certificate_base64": STANDARD.encode(&self.probe.root_ca),
                "connect_certificate_base64": STANDARD.encode(&self.probe.certificate),
                "connect_private_key_base64": STANDARD.encode(&*self.probe.private_key),
                "enable_mtls": true, "verify_name_on_connect": true,
            }}},
        });
        zenoh::Config::from_json5(&config.to_string())
            .map_err(|error| RouterError::NotReady(error.to_string()))
    }
}
