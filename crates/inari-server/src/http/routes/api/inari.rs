use axum::extract::State;
use axum::routing::{get, post};
use axum::{Json, Router};
use axum_extra::TypedHeader;
use axum_extra::headers::Authorization;
use axum_extra::headers::authorization::Bearer;
use inari_gateway::onboarding::InvitationId;
use inari_gateway::protocol::{AgentId, AgentStatus, EnrollmentRequest, EnrollmentResponse};
use inari_web::InvitationPreview;

use super::auth::ReadApi;
use super::extract::ApiPath;
use crate::error::AppError;
use crate::state::AppState;

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/enrollments", post(enroll))
        .route("/invitations/{invitation_id}", get(preview_invitation))
        .route("/agents/{agent_id}/status", get(agent_status))
}

async fn enroll(
    State(state): State<AppState>,
    authorization: Option<TypedHeader<Authorization<Bearer>>>,
    Json(request): Json<EnrollmentRequest>,
) -> Result<Json<EnrollmentResponse>, AppError> {
    let bearer_token = authorization
        .as_ref()
        .map(|TypedHeader(authorization)| authorization.token());
    state
        .managed_gateway()
        .enroll(bearer_token, request)
        .await
        .map(Json)
}

async fn preview_invitation(
    State(state): State<AppState>,
    ApiPath(invitation_id): ApiPath<InvitationId>,
) -> Result<Json<InvitationPreview>, AppError> {
    let onboarding = state.onboarding().ok_or_else(|| {
        AppError::service_unavailable("Managed gateway onboarding is not enabled.")
    })?;
    onboarding
        .invitation_preview(&invitation_id)
        .await
        .map(InvitationPreview::from)
        .map(Json)
        .map_err(Into::into)
}

async fn agent_status(
    _access: ReadApi,
    State(state): State<AppState>,
    ApiPath(agent_id): ApiPath<AgentId>,
) -> Result<Json<AgentStatus>, AppError> {
    state
        .managed_gateway()
        .agent_status(&agent_id)
        .await
        .map(Json)
}
