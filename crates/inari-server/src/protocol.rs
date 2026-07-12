use std::borrow::Cow;
use std::collections::BTreeSet;
use std::sync::Arc;

use axum::extract::{Path, State};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Serialize;

use crate::error::AppError;
use crate::managed_gateway::{
    AgentPublicationList, CommandHistoryResponse, SubmitControllerCommandRequest,
    SubmitControllerCommandResponse,
};
use crate::state::AppState;

pub trait ProtocolModule: Send + Sync {
    fn descriptor(&self) -> ProtocolDescriptor;
    fn routes(&self) -> Router<AppState>;
}

pub type DynProtocolModule = dyn ProtocolModule + Send + Sync + 'static;

pub trait IntoDynProtocolModule {
    fn into_dyn_protocol_module(self) -> Arc<DynProtocolModule>;
}

impl<P> IntoDynProtocolModule for P
where
    P: ProtocolModule + Send + Sync + 'static,
{
    fn into_dyn_protocol_module(self) -> Arc<DynProtocolModule> {
        Arc::new(self)
    }
}

impl IntoDynProtocolModule for Arc<DynProtocolModule> {
    fn into_dyn_protocol_module(self) -> Arc<DynProtocolModule> {
        self
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ProtocolDescriptor {
    pub name: Cow<'static, str>,
    pub version: Cow<'static, str>,
    pub summary: Cow<'static, str>,
    pub stage: ProtocolStage,
    pub mount_path: Cow<'static, str>,
    pub features: BTreeSet<Cow<'static, str>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProtocolStage {
    Scaffolded,
    Active,
}

#[derive(Debug, Default)]
pub struct InariProtocolModule;

impl ProtocolModule for InariProtocolModule {
    fn descriptor(&self) -> ProtocolDescriptor {
        ProtocolDescriptor {
            name: Cow::Borrowed("inari"),
            version: Cow::Borrowed("2026-07-11"),
            summary: Cow::Borrowed("Managed gateway enrollment and Zenoh command protocol."),
            stage: ProtocolStage::Active,
            mount_path: Cow::Borrowed("/api/v1/protocol"),
            features: BTreeSet::from([
                Cow::Borrowed("managed-enrollment"),
                Cow::Borrowed("durable-command-history"),
                Cow::Borrowed("zenoh-live-commands"),
                Cow::Borrowed("agent-publication-ingest"),
                Cow::Borrowed("request-budget"),
                Cow::Borrowed("consistent-http-errors"),
            ]),
        }
    }

    fn routes(&self) -> Router<AppState> {
        Router::new()
            .route("/", get(protocol_descriptor))
            .route("/commands", post(submit_controller_command))
            .route("/agents/{agent_id}/commands", get(command_history))
            .route("/agents/{agent_id}/publications", get(agent_publications))
    }
}

#[derive(Debug, Default)]
pub struct NoopProtocolModule;

impl ProtocolModule for NoopProtocolModule {
    fn descriptor(&self) -> ProtocolDescriptor {
        ProtocolDescriptor {
            name: Cow::Borrowed("inari"),
            version: Cow::Borrowed("2026-07-11"),
            summary: Cow::Borrowed("Protocol surfaces are scaffolded and ready to be filled in."),
            stage: ProtocolStage::Scaffolded,
            mount_path: Cow::Borrowed("/api/v1/protocol"),
            features: BTreeSet::from([
                Cow::Borrowed("versioned-route-boundary"),
                Cow::Borrowed("request-budget"),
                Cow::Borrowed("zenoh-session-access"),
                Cow::Borrowed("consistent-http-errors"),
            ]),
        }
    }

    fn routes(&self) -> Router<AppState> {
        Router::new()
            .route("/", get(protocol_descriptor))
            .route("/commands", post(protocol_command_stub))
            .route("/queries", post(protocol_query_stub))
    }
}

async fn protocol_descriptor(State(state): State<AppState>) -> Json<ProtocolDescriptor> {
    Json(state.protocol_descriptor())
}

async fn submit_controller_command(
    State(state): State<AppState>,
    Json(request): Json<SubmitControllerCommandRequest>,
) -> Result<Json<SubmitControllerCommandResponse>, AppError> {
    let _permit = state.acquire_protocol_permit().await?;
    state
        .managed_gateway()
        .enqueue_command(request)
        .await
        .map(Json)
}

async fn command_history(
    State(state): State<AppState>,
    Path(agent_id): Path<String>,
) -> Result<Json<CommandHistoryResponse>, AppError> {
    let _permit = state.acquire_protocol_permit().await?;
    state
        .managed_gateway()
        .list_commands(&agent_id)
        .await
        .map(Json)
}

async fn agent_publications(
    State(state): State<AppState>,
    Path(agent_id): Path<String>,
) -> Result<Json<AgentPublicationList>, AppError> {
    let _permit = state.acquire_protocol_permit().await?;
    state
        .managed_gateway()
        .list_publications(&agent_id)
        .await
        .map(Json)
}

async fn protocol_command_stub(
    State(state): State<AppState>,
) -> Result<Json<ProtocolDescriptor>, AppError> {
    let _permit = state.acquire_protocol_permit().await?;
    Err(AppError::not_implemented(
        "Command handling has a prepared boundary, but the Inari protocol is not implemented yet.",
    ))
}

async fn protocol_query_stub(
    State(state): State<AppState>,
) -> Result<Json<ProtocolDescriptor>, AppError> {
    let _permit = state.acquire_protocol_permit().await?;
    Err(AppError::not_implemented(
        "Queryable surfaces will live here once the Inari protocol is wired into the scaffold.",
    ))
}
