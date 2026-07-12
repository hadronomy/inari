use axum::extract::{FromRef, FromRequestParts};
use axum::http::request::Parts;
use axum_extra::TypedHeader;
use axum_extra::headers::Authorization;
use axum_extra::headers::authorization::Bearer;
use secrecy::SecretString;

use crate::error::AppError;
use crate::state::AppState;

pub(super) struct ReadApi;

impl<S> FromRequestParts<S> for ReadApi
where
    S: Send + Sync,
    AppState: FromRef<S>,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let TypedHeader(authorization) =
            TypedHeader::<Authorization<Bearer>>::from_request_parts(parts, state)
                .await
                .map_err(|_| AppError::unauthorized("A bearer token is required."))?;
        let state = AppState::from_ref(state);
        state
            .managed_gateway()
            .authorize_read_api(SecretString::from(authorization.token().to_owned()))?;
        Ok(Self)
    }
}
