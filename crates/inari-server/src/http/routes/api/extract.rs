use axum::extract::{FromRequest, FromRequestParts, Json, Path, Query, Request};
use axum::http::request::Parts;
use inari_gateway::protocol::{AgentId, JobId};
use serde::de::DeserializeOwned;
use sha2::{Digest, Sha256};

use crate::error::AppError;

pub(super) struct ApiPath<T>(pub(super) T);

impl<S, T> FromRequestParts<S> for ApiPath<T>
where
    S: Send + Sync,
    T: DeserializeOwned + Send,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        Path::<T>::from_request_parts(parts, state)
            .await
            .map(|Path(value)| Self(value))
            .map_err(|_| AppError::bad_request("The request path contains an invalid identifier."))
    }
}

pub(super) struct ApiJson<T>(pub(super) T);

impl<S, T> FromRequest<S> for ApiJson<T>
where
    S: Send + Sync,
    T: DeserializeOwned,
{
    type Rejection = AppError;

    async fn from_request(request: Request, state: &S) -> Result<Self, Self::Rejection> {
        Json::<T>::from_request(request, state)
            .await
            .map(|Json(value)| Self(value))
            .map_err(|rejection| AppError::bad_request(rejection.body_text()))
    }
}

pub(super) struct ApiQuery<T>(pub(super) T);

impl<S, T> FromRequestParts<S> for ApiQuery<T>
where
    S: Send + Sync,
    T: DeserializeOwned + Send,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        Query::<T>::from_request_parts(parts, state)
            .await
            .map(|Query(value)| Self(value))
            .map_err(|_| AppError::bad_request("The request query is invalid."))
    }
}

pub(super) struct IdempotencyKey(String);

impl IdempotencyKey {
    pub(super) fn job_id(&self, agent_id: &AgentId) -> Result<JobId, AppError> {
        let mut digest = Sha256::new();
        digest.update(agent_id.as_str());
        digest.update([0]);
        digest.update(self.0.as_bytes());
        format!("job_{}", &hex::encode(digest.finalize())[..32])
            .parse()
            .map_err(Into::into)
    }
}

impl<S> FromRequestParts<S> for IdempotencyKey
where
    S: Send + Sync,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, _state: &S) -> Result<Self, Self::Rejection> {
        let value = parts
            .headers
            .get("idempotency-key")
            .ok_or_else(|| AppError::bad_request("Idempotency-Key is required."))?
            .to_str()
            .map_err(|_| AppError::bad_request("Idempotency-Key must contain visible ASCII."))?;
        if value.is_empty()
            || value.len() > 128
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_graphic() || byte == b' ')
        {
            return Err(AppError::bad_request(
                "Idempotency-Key must contain 1 to 128 visible ASCII characters.",
            ));
        }
        Ok(Self(value.to_owned()))
    }
}
