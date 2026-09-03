use axum::extract::{FromRef, FromRequestParts};
use axum::http::header::AUTHORIZATION;
use axum::http::request::Parts;
use inari_gateway::protocol::ManagedWorkScope;

use super::WorkloadIdentity;
use crate::error::AppError;
use crate::state::AppState;

#[derive(Debug, Clone)]
pub struct WorkloadPrincipal {
    identity: WorkloadIdentity,
}

impl WorkloadPrincipal {
    pub fn require(&self, scope: &str) -> Result<(), AppError> {
        if self.identity.scopes.contains(scope) {
            Ok(())
        } else {
            Err(AppError::forbidden(
                "The Organization Workload Identity does not grant this operation.",
            ))
        }
    }

    pub fn require_scope(&self, scope: &ManagedWorkScope) -> Result<(), AppError> {
        if self.identity.database == scope.database
            && self.identity.company_id == scope.company_id
            && self.identity.organization_id == scope.organization_id
        {
            Ok(())
        } else {
            Err(AppError::forbidden(
                "Managed Work scope does not match the Organization Workload Identity.",
            ))
        }
    }

    #[must_use]
    pub fn identity(&self) -> &WorkloadIdentity {
        &self.identity
    }
}

impl<S> FromRequestParts<S> for WorkloadPrincipal
where
    S: Send + Sync,
    AppState: FromRef<S>,
{
    type Rejection = AppError;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        let state = AppState::from_ref(state);
        let identity = state.identity().ok_or_else(|| {
            AppError::service_unavailable("Organization Workload Identity is not enabled.")
        })?;
        let authorization = parts
            .headers
            .get(AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            .ok_or_else(|| AppError::unauthorized("A bearer access token is required."))?;
        let token = authorization
            .strip_prefix("Bearer ")
            .filter(|value| !value.is_empty())
            .ok_or_else(|| AppError::unauthorized("A bearer access token is required."))?;
        identity
            .service()
            .authenticate_workload(token)
            .map(|identity| Self { identity })
    }
}
