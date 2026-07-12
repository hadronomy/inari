use axum::extract::{Extension, State};
use axum::http::header::LOCATION;
use axum::http::{HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use axum_extra::TypedHeader;
use axum_extra::headers::Authorization;
use axum_extra::headers::authorization::Bearer;
use inari_gateway::audit::{AuditAction, AuditEvent, AuditEventDraft, AuditOutcome, AuditResource};
use inari_gateway::onboarding::InvitationId;
use inari_gateway::protocol::{
    AgentDetail, AgentId, AgentSummary, DeviceSummary, EnrollmentRequest, EnrollmentResponse,
    JobId, JobList, JobRecord, JobRequest, SiteId, SiteSummary,
};
use inari_web::InvitationPreview;
use tower_http::request_id::RequestId;

use super::extract::{ApiJson, ApiPath, ApiQuery, IdempotencyKey};
use crate::error::AppError;
use crate::identity::{Permission, Principal};
use crate::state::AppState;

pub(super) fn router() -> Router<AppState> {
    Router::new()
        .route("/enrollments", post(enroll))
        .route("/invitations/{invitation_id}", get(preview_invitation))
        .route("/sites", get(list_sites))
        .route("/agents", get(list_agents))
        .route("/agents/{agent_id}", get(get_agent))
        .route("/agents/{agent_id}/devices", get(list_devices))
        .route("/agents/{agent_id}/jobs", get(list_jobs).post(create_job))
        .route("/jobs/{job_id}", get(get_job))
        .route("/jobs/{job_id}/cancellation", put(cancel_job))
        .route("/audit-events", get(list_audit_events))
}

#[derive(Debug, serde::Deserialize)]
struct AuditFilter {
    before: Option<i64>,
    #[serde(default = "default_audit_limit")]
    limit: u16,
}

const fn default_audit_limit() -> u16 {
    100
}

async fn list_audit_events(
    principal: Principal,
    State(state): State<AppState>,
    ApiQuery(filter): ApiQuery<AuditFilter>,
) -> Result<Json<Vec<AuditEvent>>, AppError> {
    principal.require(Permission::AuditRead)?;
    if filter.limit == 0 || filter.limit > 200 {
        return Err(AppError::bad_request("Audit event limit must be between 1 and 200."));
    }
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .audit_events(filter.before, filter.limit)
        .await
        .map(Json)
}

async fn list_sites(
    principal: Principal,
    State(state): State<AppState>,
) -> Result<Json<Vec<SiteSummary>>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .sites()
        .await
        .map(Json)
}

#[derive(Debug, serde::Deserialize)]
struct AgentFilter {
    site_id: Option<SiteId>,
}

async fn list_agents(
    principal: Principal,
    State(state): State<AppState>,
    ApiQuery(filter): ApiQuery<AgentFilter>,
) -> Result<Json<Vec<AgentSummary>>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .agents(filter.site_id.as_ref())
        .await
        .map(Json)
}

async fn get_agent(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(agent_id): ApiPath<AgentId>,
) -> Result<Json<AgentDetail>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .agent(&agent_id)
        .await
        .map(Json)
}

async fn list_devices(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(agent_id): ApiPath<AgentId>,
) -> Result<Json<Vec<DeviceSummary>>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .devices(&agent_id)
        .await
        .map(Json)
}

async fn get_job(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(job_id): ApiPath<JobId>,
) -> Result<Json<JobRecord>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .job(&job_id)
        .await
        .map(Json)
}

async fn list_jobs(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(agent_id): ApiPath<AgentId>,
) -> Result<Json<JobList>, AppError> {
    principal.require(Permission::FleetRead)?;
    let _permit = state.acquire_inari_api_permit().await?;
    state
        .managed_gateway()
        .list_jobs(&agent_id)
        .await
        .map(Json)
}

async fn create_job(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(agent_id): ApiPath<AgentId>,
    idempotency_key: IdempotencyKey,
    request_id: Option<Extension<RequestId>>,
    ApiJson(request): ApiJson<JobRequest>,
) -> Result<Response, AppError> {
    principal.require(Permission::JobsWrite)?;
    let _permit = state.acquire_inari_api_permit().await?;
    let job_id = idempotency_key.job_id(&agent_id)?;
    let receipt = state
        .managed_gateway()
        .enqueue_job(&agent_id, job_id, request)
        .await?;
    state
        .managed_gateway()
        .record_audit_event(AuditEventDraft {
            organization_id: state
                .loaded_config()
                .settings
                .organization
                .id
                .clone(),
            actor_id: principal.identity().actor_id.clone(),
            action: AuditAction::JobCreated,
            resource: AuditResource::Job { job_id: receipt.job_id.clone() },
            outcome: AuditOutcome::Succeeded,
            request_id: request_id_value(request_id),
        })
        .await?;
    accepted_job(receipt)
}

async fn cancel_job(
    principal: Principal,
    State(state): State<AppState>,
    ApiPath(job_id): ApiPath<JobId>,
    request_id: Option<Extension<RequestId>>,
) -> Result<Response, AppError> {
    principal.require(Permission::JobsWrite)?;
    let _permit = state.acquire_inari_api_permit().await?;
    let receipt = state
        .managed_gateway()
        .cancel_job(&job_id)
        .await?;
    state
        .managed_gateway()
        .record_audit_event(AuditEventDraft {
            organization_id: state
                .loaded_config()
                .settings
                .organization
                .id
                .clone(),
            actor_id: principal.identity().actor_id.clone(),
            action: AuditAction::JobCancellationRequested,
            resource: AuditResource::Job { job_id },
            outcome: AuditOutcome::Succeeded,
            request_id: request_id_value(request_id),
        })
        .await?;
    accepted_job(receipt)
}

fn request_id_value(request_id: Option<Extension<RequestId>>) -> Option<String> {
    request_id.and_then(|Extension(request_id)| {
        request_id
            .header_value()
            .to_str()
            .ok()
            .map(str::to_owned)
    })
}

fn accepted_job(receipt: inari_gateway::protocol::JobReceipt) -> Result<Response, AppError> {
    let location = HeaderValue::from_str(&format!("/api/inari/v1/jobs/{}", receipt.job_id))
        .map_err(|source| {
            AppError::internal("job_location", "The job resource location is invalid.")
                .with_source(source)
        })?;
    Ok((StatusCode::ACCEPTED, [(LOCATION, location)], Json(receipt)).into_response())
}

async fn enroll(
    State(state): State<AppState>,
    authorization: Option<TypedHeader<Authorization<Bearer>>>,
    ApiJson(request): ApiJson<EnrollmentRequest>,
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
