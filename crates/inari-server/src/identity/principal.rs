use axum::extract::{FromRef, FromRequestParts};
use axum::http::request::Parts;
use tower_sessions::Session;

use super::{Permission, SessionIdentity};
use crate::error::AppError;
use crate::state::AppState;

pub const SESSION_IDENTITY_KEY: &str = "identity";

#[derive(Debug, Clone)]
pub struct Principal {
    identity: SessionIdentity,
}

impl Principal {
    pub async fn from_session(session: &Session) -> Result<Self, AppError> {
        let identity = session
            .get::<SessionIdentity>(SESSION_IDENTITY_KEY)
            .await
            .map_err(|source| {
                AppError::internal("session_read", "The authenticated session could not be read.")
                    .with_source(source)
            })?
            .ok_or_else(|| AppError::unauthorized("An authenticated session is required."))?;

        if identity.expires_at <= chrono::Utc::now() {
            session
                .flush()
                .await
                .map_err(|source| {
                    AppError::internal(
                        "session_expiry",
                        "The expired session could not be removed.",
                    )
                    .with_source(source)
                })?;
            return Err(AppError::unauthorized("The authenticated session has expired."));
        }

        Ok(Self { identity })
    }

    #[must_use]
    pub fn identity(&self) -> &SessionIdentity {
        &self.identity
    }

    pub fn require(&self, permission: Permission) -> Result<(), AppError> {
        if self.identity.grants(permission) {
            Ok(())
        } else {
            Err(AppError::forbidden(
                "The authenticated identity does not have permission for this operation.",
            ))
        }
    }
}

impl<S> FromRequestParts<S> for Principal
where
    S: Send + Sync,
    AppState: FromRef<S>,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let session = Session::from_request_parts(parts, state)
            .await
            .map_err(|_| AppError::unauthorized("An authenticated session is required."))?;
        Self::from_session(&session).await
    }
}
