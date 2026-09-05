use std::sync::Arc;
use std::time::{Duration, Instant};

use aes_gcm::aead::{Aead, Payload};
use aes_gcm::{Aes256Gcm, KeyInit, Nonce};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use futures_util::future::BoxFuture;
use inari_gateway::NewManagedPayload;
use secrecy::{ExposeSecret, SecretString};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::AsyncReadExt;
use tokio::sync::Mutex;
use zeroize::Zeroizing;

use crate::config::ManagedGatewayPayloadProtectionConfig;
use crate::error::{AppError, AppResult};

const DATA_KEY_BYTES: usize = 32;
const NONCE_BYTES: usize = 12;
const MAX_OPENBAO_RESPONSE_BYTES: usize = 64 * 1024;

#[derive(Clone, Debug)]
pub struct TransitWrappedKey {
    pub ciphertext: String,
    pub key_version: u32,
}

pub trait ManagedPayloadKeyWrapper: Send + Sync {
    fn wrap<'a>(
        &'a self,
        data_key: &'a [u8],
        authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<TransitWrappedKey>>;

    fn unwrap<'a>(
        &'a self,
        wrapped_data_key: &'a str,
        authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<Zeroizing<Vec<u8>>>>;
}

pub type ManagedPayloadKeyWrapperHandle = Arc<dyn ManagedPayloadKeyWrapper>;

#[derive(Clone)]
pub struct ManagedPayloadProtector {
    wrapper: ManagedPayloadKeyWrapperHandle,
}

impl std::fmt::Debug for ManagedPayloadProtector {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ManagedPayloadProtector")
            .finish_non_exhaustive()
    }
}

impl ManagedPayloadProtector {
    #[must_use]
    pub fn new(wrapper: ManagedPayloadKeyWrapperHandle) -> Self {
        Self { wrapper }
    }

    pub(crate) async fn prepare(
        &self,
        authenticated_data: Vec<u8>,
    ) -> AppResult<PreparedManagedPayload> {
        let mut data_key = Zeroizing::new([0_u8; DATA_KEY_BYTES]);
        getrandom::fill(data_key.as_mut()).map_err(|source| {
            AppError::service_unavailable("A Managed Payload data key could not be created.")
                .with_source(source)
        })?;
        let wrapped = self
            .wrapper
            .wrap(data_key.as_ref(), &authenticated_data)
            .await
            .inspect_err(
                |error| tracing::error!(error = %error, "Managed Payload key wrapping failed"),
            )?;
        Ok(PreparedManagedPayload { data_key, wrapped, authenticated_data })
    }

    pub(crate) async fn open(
        &self,
        protected: &NewManagedPayload,
        authenticated_data: &[u8],
    ) -> AppResult<Zeroizing<Vec<u8>>> {
        let digest: [u8; 32] = Sha256::digest(authenticated_data).into();
        if digest != protected.authenticated_data_digest
            || transit_key_version(&protected.wrapped_data_key)? != protected.wrapping_key_version
        {
            return Err(AppError::service_unavailable(
                "Managed Payload metadata does not match its protection.",
            ));
        }
        let data_key = self
            .wrapper
            .unwrap(&protected.wrapped_data_key, authenticated_data)
            .await
            .inspect_err(
                |error| tracing::error!(error = %error, "Managed Payload key unwrapping failed"),
            )?;
        let cipher = Aes256Gcm::new_from_slice(&data_key).map_err(|_| {
            AppError::service_unavailable("The Managed Payload data key is invalid.")
        })?;
        let plaintext = Zeroizing::new(
            cipher
                .decrypt(
                    &Nonce::from(protected.nonce),
                    Payload { msg: &protected.ciphertext, aad: authenticated_data },
                )
                .map_err(|_| {
                    AppError::service_unavailable("Managed Payload authentication failed.")
                })?,
        );
        if i64::try_from(plaintext.len()).ok() != Some(protected.plaintext_bytes) {
            return Err(AppError::service_unavailable(
                "The Managed Payload size does not match its metadata.",
            ));
        }
        Ok(plaintext)
    }
}

pub(crate) struct PreparedManagedPayload {
    data_key: Zeroizing<[u8; DATA_KEY_BYTES]>,
    wrapped: TransitWrappedKey,
    authenticated_data: Vec<u8>,
}

impl PreparedManagedPayload {
    pub(crate) fn seal(self, plaintext: &[u8]) -> AppResult<NewManagedPayload> {
        let mut nonce = [0_u8; NONCE_BYTES];
        getrandom::fill(&mut nonce).map_err(|source| {
            AppError::service_unavailable("A Managed Payload nonce could not be created.")
                .with_source(source)
        })?;
        let cipher = Aes256Gcm::new_from_slice(self.data_key.as_ref()).map_err(|_| {
            AppError::service_unavailable("The Managed Payload cipher could not be initialized.")
        })?;
        let ciphertext = cipher
            .encrypt(&Nonce::from(nonce), Payload { msg: plaintext, aad: &self.authenticated_data })
            .map_err(|_| {
                AppError::service_unavailable("The Managed Payload could not be encrypted.")
            })?;
        Ok(NewManagedPayload {
            ciphertext,
            nonce,
            wrapped_data_key: self.wrapped.ciphertext,
            wrapping_key_version: self.wrapped.key_version,
            authenticated_data_digest: Sha256::digest(&self.authenticated_data).into(),
            plaintext_bytes: i64::try_from(plaintext.len())
                .map_err(|_| AppError::bad_request("Managed Payload size is out of range."))?,
        })
    }
}

pub struct OpenBaoTransitKeyWrapper {
    client: reqwest::Client,
    config: ManagedGatewayPayloadProtectionConfig,
    token: Mutex<Option<CachedToken>>,
}

impl std::fmt::Debug for OpenBaoTransitKeyWrapper {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("OpenBaoTransitKeyWrapper")
            .field("address", &self.config.address)
            .field("transit_mount", &self.config.transit_mount)
            .field("transit_key_name", &self.config.transit_key_name)
            .finish_non_exhaustive()
    }
}

impl OpenBaoTransitKeyWrapper {
    pub async fn load(config: ManagedGatewayPayloadProtectionConfig) -> AppResult<Self> {
        let mut builder = reqwest::Client::builder()
            .timeout(config.request_timeout)
            .redirect(reqwest::redirect::Policy::none());
        if let Some(path) = &config.ca_certificate_file {
            let pem = tokio::fs::read(path)
                .await
                .map_err(|source| {
                    AppError::service_unavailable(
                        "The OpenBao certificate authority file could not be read.",
                    )
                    .with_source(source)
                })?;
            let certificate = reqwest::Certificate::from_pem(&pem).map_err(|source| {
                AppError::service_unavailable("The OpenBao certificate authority file is invalid.")
                    .with_source(source)
            })?;
            builder = builder.add_root_certificate(certificate);
        }
        let client = builder.build().map_err(|source| {
            AppError::service_unavailable("The OpenBao client could not be initialized.")
                .with_source(source)
        })?;
        Ok(Self { client, config, token: Mutex::new(None) })
    }

    async fn token(&self) -> AppResult<SecretString> {
        let mut cached = self.token.lock().await;
        if let Some(token) = cached.as_ref()
            && token.refresh_at > Instant::now()
        {
            return Ok(token.value.clone());
        }
        let file = tokio::fs::File::open(&self.config.service_account_token_file)
            .await
            .map_err(|source| {
                AppError::service_unavailable(
                    "The Controller workload identity token could not be read.",
                )
                .with_source(source)
            })?;
        let mut workload_jwt = Zeroizing::new(String::new());
        file.take((MAX_OPENBAO_RESPONSE_BYTES + 1) as u64)
            .read_to_string(&mut workload_jwt)
            .await
            .map_err(|source| {
                AppError::service_unavailable(
                    "The Controller workload identity token could not be read.",
                )
                .with_source(source)
            })?;
        if workload_jwt.len() > MAX_OPENBAO_RESPONSE_BYTES || workload_jwt.trim().is_empty() {
            return Err(AppError::service_unavailable(
                "The Controller workload identity token is invalid.",
            ));
        }
        let role = self
            .config
            .kubernetes_role
            .as_deref()
            .ok_or_else(|| AppError::service_unavailable("The OpenBao role is not configured."))?;
        let response: OpenBaoLoginResponse = self
            .post(
                &format!("v1/auth/{}/login", self.config.kubernetes_auth_mount),
                None,
                &OpenBaoLoginRequest { role, jwt: workload_jwt.trim() },
            )
            .await?;
        if response.auth.client_token.is_empty() || response.auth.lease_duration == 0 {
            return Err(AppError::service_unavailable(
                "OpenBao returned invalid authentication data.",
            ));
        }
        let refresh_seconds = response
            .auth
            .lease_duration
            .saturating_mul(4)
            .checked_div(5)
            .unwrap_or(1)
            .max(1);
        let token = SecretString::from(response.auth.client_token);
        *cached = Some(CachedToken {
            value: token.clone(),
            refresh_at: Instant::now()
                .checked_add(Duration::from_secs(refresh_seconds))
                .ok_or_else(|| {
                    AppError::service_unavailable("OpenBao returned an invalid token lifetime.")
                })?,
        });
        Ok(token)
    }

    async fn post<T, B>(&self, path: &str, token: Option<&SecretString>, body: &B) -> AppResult<T>
    where
        T: DeserializeOwned,
        B: Serialize + ?Sized,
    {
        let address = self
            .config
            .address
            .as_ref()
            .ok_or_else(|| {
                AppError::service_unavailable("The OpenBao address is not configured.")
            })?;
        let url = address.join(path).map_err(|source| {
            AppError::service_unavailable("The OpenBao request URL is invalid.").with_source(source)
        })?;
        let mut request = self.client.post(url).json(body);
        if let Some(namespace) = self.config.namespace.as_deref() {
            request = request.header("X-Vault-Namespace", namespace);
        }
        if let Some(token) = token {
            request = request.header("X-Vault-Token", token.expose_secret());
        }
        let mut response = request.send().await.map_err(|source| {
            AppError::service_unavailable("OpenBao did not accept the request.").with_source(source)
        })?;
        let status = response.status();
        if token.is_some()
            && matches!(status, reqwest::StatusCode::UNAUTHORIZED | reqwest::StatusCode::FORBIDDEN)
        {
            self.token.lock().await.take();
        }
        if !status.is_success()
            || response
                .content_length()
                .is_some_and(|length| length > MAX_OPENBAO_RESPONSE_BYTES as u64)
        {
            return Err(AppError::service_unavailable(
                "OpenBao returned an unsuccessful response.",
            ));
        }
        let mut bytes = Zeroizing::new(Vec::with_capacity(
            response
                .content_length()
                .and_then(|length| usize::try_from(length).ok())
                .unwrap_or(0),
        ));
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|source| {
                AppError::service_unavailable("The OpenBao response could not be read.")
                    .with_source(source)
            })?
        {
            if bytes
                .len()
                .checked_add(chunk.len())
                .is_none_or(|length| length > MAX_OPENBAO_RESPONSE_BYTES)
            {
                return Err(AppError::service_unavailable(
                    "The OpenBao response exceeded its size limit.",
                ));
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&bytes).map_err(|source| {
            AppError::service_unavailable("OpenBao returned an invalid response.")
                .with_source(source)
        })
    }
}

impl ManagedPayloadKeyWrapper for OpenBaoTransitKeyWrapper {
    fn wrap<'a>(
        &'a self,
        data_key: &'a [u8],
        authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<TransitWrappedKey>> {
        Box::pin(async move {
            let token = self.token().await?;
            let plaintext = Zeroizing::new(STANDARD.encode(data_key));
            let response: OpenBaoEncryptResponse = self
                .post(
                    &format!(
                        "v1/{}/encrypt/{}",
                        self.config.transit_mount, self.config.transit_key_name
                    ),
                    Some(&token),
                    &OpenBaoEncryptRequest {
                        plaintext: plaintext.as_str(),
                        associated_data: STANDARD.encode(authenticated_data),
                    },
                )
                .await?;
            let key_version = transit_key_version(&response.data.ciphertext)?;
            Ok(TransitWrappedKey { ciphertext: response.data.ciphertext, key_version })
        })
    }

    fn unwrap<'a>(
        &'a self,
        wrapped_data_key: &'a str,
        authenticated_data: &'a [u8],
    ) -> BoxFuture<'a, AppResult<Zeroizing<Vec<u8>>>> {
        Box::pin(async move {
            let token = self.token().await?;
            let response: OpenBaoDecryptResponse = self
                .post(
                    &format!(
                        "v1/{}/decrypt/{}",
                        self.config.transit_mount, self.config.transit_key_name
                    ),
                    Some(&token),
                    &OpenBaoDecryptRequest {
                        ciphertext: wrapped_data_key,
                        associated_data: STANDARD.encode(authenticated_data),
                    },
                )
                .await?;
            let encoded_key = Zeroizing::new(response.data.plaintext);
            let data_key = Zeroizing::new(
                STANDARD
                    .decode(encoded_key.as_bytes())
                    .map_err(|_| {
                        AppError::service_unavailable("OpenBao returned an invalid data key.")
                    })?,
            );
            if data_key.len() != DATA_KEY_BYTES {
                return Err(AppError::service_unavailable(
                    "OpenBao returned an invalid data key size.",
                ));
            }
            Ok(data_key)
        })
    }
}

struct CachedToken {
    value: SecretString,
    refresh_at: Instant,
}

#[derive(Serialize)]
struct OpenBaoLoginRequest<'a> {
    role: &'a str,
    jwt: &'a str,
}

#[derive(Deserialize)]
struct OpenBaoLoginResponse {
    auth: OpenBaoAuth,
}

#[derive(Deserialize)]
struct OpenBaoAuth {
    client_token: String,
    lease_duration: u64,
}

#[derive(Serialize)]
struct OpenBaoEncryptRequest<'a> {
    plaintext: &'a str,
    associated_data: String,
}

#[derive(Deserialize)]
struct OpenBaoEncryptResponse {
    data: OpenBaoEncryptData,
}

#[derive(Deserialize)]
struct OpenBaoEncryptData {
    ciphertext: String,
}

#[derive(Serialize)]
struct OpenBaoDecryptRequest<'a> {
    ciphertext: &'a str,
    associated_data: String,
}

#[derive(Deserialize)]
struct OpenBaoDecryptResponse {
    data: OpenBaoDecryptData,
}

#[derive(Deserialize)]
struct OpenBaoDecryptData {
    plaintext: String,
}

fn transit_key_version(ciphertext: &str) -> AppResult<u32> {
    let (version, body) = ciphertext
        .strip_prefix("vault:v")
        .and_then(|value| value.split_once(':'))
        .ok_or_else(|| {
            AppError::service_unavailable("OpenBao returned an invalid Transit ciphertext.")
        })?;
    let version = version
        .parse::<u32>()
        .ok()
        .filter(|version| *version > 0 && *version <= i32::MAX as u32)
        .filter(|_| !body.is_empty() && STANDARD.decode(body).is_ok())
        .ok_or_else(|| {
            AppError::service_unavailable("OpenBao returned an invalid Transit ciphertext.")
        })?;
    Ok(version)
}

#[cfg(test)]
mod http_tests;

#[cfg(test)]
mod tests {
    use futures_util::future::BoxFuture;
    use std::sync::{Arc, Mutex};

    use aes_gcm::aead::{Aead, Payload};
    use aes_gcm::{Aes256Gcm, KeyInit, Nonce};

    use super::{
        ManagedPayloadKeyWrapper, ManagedPayloadProtector, TransitWrappedKey, transit_key_version,
    };
    use crate::error::AppResult;

    #[derive(Default)]
    struct RecordingWrapper {
        key: Mutex<Vec<u8>>,
    }

    impl ManagedPayloadKeyWrapper for RecordingWrapper {
        fn wrap<'a>(
            &'a self,
            data_key: &'a [u8],
            _authenticated_data: &'a [u8],
        ) -> BoxFuture<'a, AppResult<TransitWrappedKey>> {
            Box::pin(async move {
                *self
                    .key
                    .lock()
                    .expect("test key lock should be available") = data_key.to_vec();
                Ok(TransitWrappedKey { ciphertext: "vault:v7:test".into(), key_version: 7 })
            })
        }

        fn unwrap<'a>(
            &'a self,
            _wrapped_data_key: &'a str,
            _authenticated_data: &'a [u8],
        ) -> std::pin::Pin<
            Box<dyn Future<Output = AppResult<zeroize::Zeroizing<Vec<u8>>>> + Send + 'a>,
        > {
            Box::pin(async move { Ok(zeroize::Zeroizing::new(self.key.lock().unwrap().clone())) })
        }
    }

    #[tokio::test]
    async fn payload_encryption_uses_random_aes_gcm_and_transit_wrapping() {
        let wrapper = Arc::new(RecordingWrapper::default());
        let protector = ManagedPayloadProtector::new(wrapper.clone());
        let aad = br#"{"managed_work_id":"mw_test"}"#;

        let protected = protector
            .prepare(aad.to_vec())
            .await
            .expect("payload key should wrap")
            .seal(b"private report")
            .expect("payload should encrypt");

        assert_ne!(protected.ciphertext, b"private report");
        assert_eq!(protected.wrapping_key_version, 7);
        let key = wrapper
            .key
            .lock()
            .expect("test key lock should be available")
            .clone();
        let cipher = Aes256Gcm::new_from_slice(&key).expect("test key should be valid");
        let plaintext = cipher
            .decrypt(&Nonce::from(protected.nonce), Payload { msg: &protected.ciphertext, aad })
            .expect("protected payload should decrypt");
        assert_eq!(plaintext, b"private report");
        assert_eq!(
            &*protector
                .open(&protected, aad)
                .await
                .unwrap(),
            b"private report"
        );
        assert!(
            protector
                .open(&protected, b"another Managed Work")
                .await
                .is_err()
        );
        let mut altered = protected.clone();
        altered.ciphertext[0] ^= 1;
        assert!(
            protector
                .open(&altered, aad)
                .await
                .is_err()
        );
        let mut altered = protected.clone();
        altered.nonce[0] ^= 1;
        assert!(
            protector
                .open(&altered, aad)
                .await
                .is_err()
        );
        let mut altered = protected.clone();
        altered.wrapping_key_version += 1;
        assert!(
            protector
                .open(&altered, aad)
                .await
                .is_err()
        );
        let mut altered = protected.clone();
        altered.plaintext_bytes += 1;
        assert!(
            protector
                .open(&altered, aad)
                .await
                .is_err()
        );
        let second = protector
            .prepare(aad.to_vec())
            .await
            .unwrap()
            .seal(b"private report")
            .unwrap();
        assert_ne!(protected.ciphertext, second.ciphertext);
        assert_ne!(protected.nonce, second.nonce);
    }

    #[test]
    fn transit_ciphertext_carries_the_key_version() {
        assert_eq!(transit_key_version("vault:v12:d3JhcHBlZA==").unwrap(), 12);
        for invalid in [
            "not-vault",
            "vault:v0:dGVzdA==",
            "vault:v1:",
            "vault:v1:%%%%",
            "vault:v999999999999:dGVzdA==",
        ] {
            assert!(transit_key_version(invalid).is_err());
        }
    }
}
