use axum::Router;
use axum::extract::{Request, State};
use axum::http::Method;
use axum::middleware::{self, Next};
use axum::response::Response;
use inari_gateway::audit::{AuditAction, AuditEventDraft, AuditOutcome, AuditResource};
use tower_sessions::Session;

use crate::error::AppError;
use crate::identity::{Permission, Principal};
use crate::state::AppState;
use crate::zenoh::rest;

pub(super) fn router(state: &AppState) -> Router<AppState> {
    rest::router(state).route_layer(middleware::from_fn_with_state(state.clone(), authorize))
}

async fn authorize(
    session: Session,
    State(state): State<AppState>,
    request: Request,
    next: Next,
) -> Result<Response, AppError> {
    let principal = Principal::from_session(&session).await?;
    let (permission, action) = match *request.method() {
        Method::GET | Method::HEAD => (Permission::ZenohRead, AuditAction::ZenohRead),
        _ => (Permission::ZenohWrite, AuditAction::ZenohWrite),
    };
    let resource = AuditResource::ZenohSelector {
        selector: request
            .uri()
            .path()
            .strip_prefix("/api/zenoh/v1/")
            .unwrap_or("")
            .to_owned(),
    };
    if let Err(error) = principal.require(permission) {
        record_audit_event(&state, &principal, action, resource, AuditOutcome::Denied).await;
        return Err(error);
    }

    let response = next.run(request).await;
    let outcome =
        if response.status().is_success() { AuditOutcome::Succeeded } else { AuditOutcome::Failed };
    record_audit_event(&state, &principal, action, resource, outcome).await;
    Ok(response)
}

async fn record_audit_event(
    state: &AppState,
    principal: &Principal,
    action: AuditAction,
    resource: AuditResource,
    outcome: AuditOutcome,
) {
    if state.onboarding().is_none() {
        return;
    }
    let result = state
        .managed_gateway()
        .record_audit_event(AuditEventDraft {
            organization_id: state
                .loaded_config()
                .settings
                .organization
                .id
                .clone(),
            actor_id: principal.identity().actor_id.clone(),
            action,
            resource,
            outcome,
            request_id: None,
        })
        .await;
    if let Err(error) = result {
        tracing::warn!(error = %error, "Zenoh HTTP audit event could not be persisted");
    }
}
