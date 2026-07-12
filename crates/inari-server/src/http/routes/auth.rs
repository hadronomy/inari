use axum::Router;
use axum::extract::{Query, State};
use axum::response::Redirect;
use axum::routing::{get, post};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use tower_sessions::Session;

use crate::error::{AppError, AppResult};
use crate::identity::{PendingLogin, SESSION_IDENTITY_KEY};
use crate::state::AppState;

const PENDING_LOGIN_KEY: &str = "pending_oidc_login";

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/auth/login", get(login))
        .route("/auth/callback", get(callback))
        .route("/auth/logout", post(logout))
}

#[derive(Debug, Deserialize)]
struct LoginQuery {
    return_to: Option<String>,
}

async fn login(
    State(state): State<AppState>,
    session: Session,
    Query(query): Query<LoginQuery>,
) -> AppResult<Redirect> {
    let identity = state
        .identity()
        .ok_or_else(|| AppError::service_unavailable("Organization login is not configured."))?;
    let return_to = safe_return_path(query.return_to.as_deref());
    let challenge = identity
        .service()
        .begin_login(return_to);
    session
        .insert(PENDING_LOGIN_KEY, &challenge.pending)
        .await
        .map_err(|source| {
            AppError::internal("session_write", "The login session could not be started.")
                .with_source(source)
        })?;
    Ok(Redirect::to(challenge.authorize_url.as_str()))
}

#[derive(Debug, Deserialize)]
struct CallbackQuery {
    code: Option<String>,
    state: Option<String>,
    error: Option<String>,
}

async fn callback(
    State(state): State<AppState>,
    session: Session,
    Query(query): Query<CallbackQuery>,
) -> AppResult<Redirect> {
    if let Some(error) = query.error {
        tracing::info!(oidc_error = %error, "OIDC provider declined authentication");
        return Err(AppError::unauthorized("Organization login was not completed."));
    }
    let pending = session
        .remove::<PendingLogin>(PENDING_LOGIN_KEY)
        .await
        .map_err(|source| {
            AppError::internal("session_read", "The login session could not be read.")
                .with_source(source)
        })?
        .ok_or_else(|| AppError::unauthorized("The login request is missing or has expired."))?;
    let state_value = query
        .state
        .ok_or_else(|| AppError::unauthorized("OIDC callback state is missing."))?;
    if !constant_time_eq(&state_value, &pending.state) {
        return Err(AppError::unauthorized("OIDC callback state did not match."));
    }
    let code = query
        .code
        .ok_or_else(|| AppError::unauthorized("OIDC authorization code is missing."))?;
    let identity = state
        .identity()
        .ok_or_else(|| AppError::service_unavailable("Organization login is not configured."))?;
    let authenticated = identity
        .service()
        .complete_login(code, &pending)
        .await?;
    session
        .cycle_id()
        .await
        .map_err(|source| {
            AppError::internal("session_rotation", "The login session could not be rotated.")
                .with_source(source)
        })?;
    session
        .insert(SESSION_IDENTITY_KEY, authenticated)
        .await
        .map_err(|source| {
            AppError::internal("session_write", "The authenticated session could not be stored.")
                .with_source(source)
        })?;
    Ok(Redirect::to(&pending.return_to))
}

async fn logout(session: Session) -> AppResult<Redirect> {
    session
        .flush()
        .await
        .map_err(|source| {
            AppError::internal("session_logout", "The authenticated session could not be removed.")
                .with_source(source)
        })?;
    Ok(Redirect::to("/"))
}

fn safe_return_path(candidate: Option<&str>) -> String {
    candidate
        .filter(|path| path.starts_with('/') && !path.starts_with("//"))
        .filter(|path| !path.chars().any(char::is_control))
        .unwrap_or("/")
        .to_owned()
}

fn constant_time_eq(left: &str, right: &str) -> bool {
    let left = Sha256::digest(left.as_bytes());
    let right = Sha256::digest(right.as_bytes());
    bool::from(left.as_slice().ct_eq(right.as_slice()))
}

#[cfg(test)]
mod tests {
    use super::safe_return_path;

    #[test]
    fn return_paths_must_be_local_and_absolute() {
        assert_eq!(safe_return_path(Some("/onboarding")), "/onboarding");
        assert_eq!(safe_return_path(Some("//attacker.example")), "/");
        assert_eq!(safe_return_path(Some("https://attacker.example")), "/");
        assert_eq!(safe_return_path(Some("/safe\nlocation")), "/");
    }
}
