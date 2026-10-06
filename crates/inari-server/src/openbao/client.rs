use std::time::{Duration, Instant};

use secrecy::{ExposeSecret, SecretString};
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use tokio::io::AsyncReadExt;
use tokio::sync::Mutex;
use zeroize::Zeroizing;

use crate::config::OpenBaoConfig;
use crate::error::{AppError, AppResult};

const MAX_OPENBAO_RESPONSE_BYTES: usize = 64 * 1024;

pub struct OpenBaoClient {
    client: reqwest::Client,
    config: OpenBaoConfig,
    token: Mutex<Option<CachedToken>>,
}

impl std::fmt::Debug for OpenBaoClient {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("OpenBaoClient")
            .field("address", &self.config.address)
            .finish_non_exhaustive()
    }
}

impl OpenBaoClient {
    pub async fn load(config: OpenBaoConfig) -> AppResult<Self> {
        config.validate(true)?;
        Self::load_inner(config).await
    }

    #[cfg(test)]
    pub(crate) async fn load_test(config: OpenBaoConfig) -> AppResult<Self> {
        Self::load_inner(config).await
    }

    async fn load_inner(config: OpenBaoConfig) -> AppResult<Self> {
        let mut builder = reqwest::Client::builder()
            .no_proxy()
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
            .request(
                reqwest::Method::POST,
                &format!("v1/auth/{}/login", self.config.kubernetes_auth_mount),
                None,
                Some(&OpenBaoLoginRequest { role, jwt: workload_jwt.trim() }),
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

    pub(crate) async fn post<T, B>(&self, path: &str, body: &B) -> AppResult<T>
    where
        T: DeserializeOwned,
        B: Serialize + ?Sized,
    {
        let token = self.token().await?;
        self.request(reqwest::Method::POST, path, Some(&token), Some(body))
            .await
    }

    pub(crate) async fn get<T: DeserializeOwned>(&self, path: &str) -> AppResult<T> {
        let token = self.token().await?;
        self.request::<T, ()>(reqwest::Method::GET, path, Some(&token), None)
            .await
    }

    async fn request<T, B>(
        &self,
        method: reqwest::Method,
        path: &str,
        token: Option<&SecretString>,
        body: Option<&B>,
    ) -> AppResult<T>
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
        let mut request = self.client.request(method, url);
        if let Some(body) = body {
            request = request.json(body);
        }
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

#[cfg(test)]
mod live_tests;
