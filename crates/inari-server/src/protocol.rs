use std::{borrow::Cow, collections::BTreeSet};

use axum::{
    Json, Router,
    extract::State,
    routing::{get, post},
};
use serde::Serialize;

use crate::{error::AppError, state::AppState};

pub trait ProtocolModule: Send + Sync {
    fn descriptor(&self) -> ProtocolDescriptor;
    fn routes(&self) -> Router<AppState>;
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
pub struct NoopProtocolModule;

impl ProtocolModule for NoopProtocolModule {
    fn descriptor(&self) -> ProtocolDescriptor {
        ProtocolDescriptor {
            name: Cow::Borrowed("inari"),
            version: Cow::Borrowed("2026-04-18"),
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
