use std::{
    borrow::Cow,
    collections::BTreeMap,
    fmt,
    sync::Arc,
    time::{Duration, Instant},
};

use chrono::{DateTime, Utc};
use serde::Serialize;
use tokio::sync::{OwnedSemaphorePermit, Semaphore, watch};

use crate::{
    config::LoadedConfig,
    error::AppError,
    protocol::{ProtocolDescriptor, ProtocolModule},
    zenoh::{ZenohConnectionState, ZenohHandle, ZenohStatus},
};

#[derive(Clone)]
pub struct AppState {
    inner: Arc<AppStateInner>,
}

struct AppStateInner {
    loaded: LoadedConfig,
    started_at: DateTime<Utc>,
    started_at_instant: Instant,
    readiness: watch::Sender<ReadinessSnapshot>,
    zenoh: ZenohHandle,
    protocol: Arc<dyn ProtocolModule>,
    protocol_budget: Arc<Semaphore>,
}

impl fmt::Debug for AppState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AppState")
            .field("origin", &self.inner.loaded.origin)
            .field("started_at", &self.inner.started_at)
            .field("readiness", &self.readiness_snapshot())
            .finish_non_exhaustive()
    }
}

impl AppState {
    #[must_use]
    pub fn new(
        loaded: LoadedConfig,
        zenoh: ZenohHandle,
        protocol: Arc<dyn ProtocolModule>,
    ) -> Self {
        let readiness = ReadinessSnapshot::from_zenoh_status(&zenoh.status_snapshot());
        let (readiness_sender, _) = watch::channel(readiness);

        Self {
            inner: Arc::new(AppStateInner {
                protocol_budget: Arc::new(Semaphore::new(
                    loaded.settings.protocol.max_concurrent_requests,
                )),
                loaded,
                started_at: Utc::now(),
                started_at_instant: Instant::now(),
                readiness: readiness_sender,
                zenoh,
                protocol,
            }),
        }
    }

    #[must_use]
    pub fn loaded_config(&self) -> &LoadedConfig {
        &self.inner.loaded
    }

    #[must_use]
    pub fn started_at(&self) -> DateTime<Utc> {
        self.inner.started_at
    }

    #[must_use]
    pub fn uptime(&self) -> Duration {
        self.inner.started_at_instant.elapsed()
    }

    #[must_use]
    pub fn zenoh(&self) -> &ZenohHandle {
        &self.inner.zenoh
    }

    #[must_use]
    pub fn protocol_descriptor(&self) -> ProtocolDescriptor {
        self.inner.protocol.descriptor()
    }

    pub fn protocol_routes(&self) -> axum::Router<Self> {
        self.inner.protocol.routes()
    }

    #[must_use]
    pub fn readiness_snapshot(&self) -> ReadinessSnapshot {
        self.inner.readiness.borrow().clone()
    }

    #[must_use]
    pub fn subscribe_readiness(&self) -> watch::Receiver<ReadinessSnapshot> {
        self.inner.readiness.subscribe()
    }

    pub fn update_zenoh_readiness(&self, status: &ZenohStatus) {
        self.inner.readiness.send_replace(ReadinessSnapshot::from_zenoh_status(status));
    }

    pub async fn acquire_protocol_permit(&self) -> Result<OwnedSemaphorePermit, AppError> {
        Arc::clone(&self.inner.protocol_budget).acquire_owned().await.map_err(|_| {
            AppError::service_unavailable("The protocol execution budget is no longer available.")
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReadinessLevel {
    Ready,
    Starting,
    Degraded,
    Disabled,
}

impl fmt::Display for ReadinessLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Ready => f.write_str("ready"),
            Self::Starting => f.write_str("starting"),
            Self::Degraded => f.write_str("degraded"),
            Self::Disabled => f.write_str("disabled"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ComponentReadiness {
    pub level: ReadinessLevel,
    pub summary: Cow<'static, str>,
}

impl ComponentReadiness {
    #[must_use]
    pub fn ready(summary: impl Into<Cow<'static, str>>) -> Self {
        Self { level: ReadinessLevel::Ready, summary: summary.into() }
    }

    #[must_use]
    pub fn starting(summary: impl Into<Cow<'static, str>>) -> Self {
        Self { level: ReadinessLevel::Starting, summary: summary.into() }
    }

    #[must_use]
    pub fn degraded(summary: impl Into<Cow<'static, str>>) -> Self {
        Self { level: ReadinessLevel::Degraded, summary: summary.into() }
    }

    #[must_use]
    pub fn disabled(summary: impl Into<Cow<'static, str>>) -> Self {
        Self { level: ReadinessLevel::Disabled, summary: summary.into() }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReadinessSnapshot {
    pub ready: bool,
    pub observed_at: DateTime<Utc>,
    pub components: BTreeMap<Cow<'static, str>, ComponentReadiness>,
}

impl ReadinessSnapshot {
    #[must_use]
    pub fn from_zenoh_status(status: &ZenohStatus) -> Self {
        let mut components = BTreeMap::from([(
            Cow::Borrowed("http"),
            ComponentReadiness::ready("HTTP server bootstrap completed."),
        )]);

        let zenoh = match status.state {
            ZenohConnectionState::Disabled => {
                ComponentReadiness::disabled("Zenoh integration is disabled.")
            }
            ZenohConnectionState::Starting | ZenohConnectionState::Reconnecting => {
                ComponentReadiness::starting(
                    status
                        .message
                        .clone()
                        .unwrap_or_else(|| "Zenoh session is still starting.".into()),
                )
            }
            ZenohConnectionState::Connected => {
                ComponentReadiness::ready("Zenoh session is connected.")
            }
            ZenohConnectionState::Degraded => ComponentReadiness::degraded(
                status.message.clone().unwrap_or_else(|| "Zenoh session is unavailable.".into()),
            ),
            ZenohConnectionState::ShuttingDown => {
                ComponentReadiness::degraded("Zenoh session is shutting down.")
            }
        };
        components.insert(Cow::Borrowed("zenoh"), zenoh);

        let ready = components.values().all(|component| {
            matches!(component.level, ReadinessLevel::Ready | ReadinessLevel::Disabled)
        });

        Self { ready, observed_at: Utc::now(), components }
    }
}

impl fmt::Display for ReadinessSnapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let overall = if self.ready { "ready" } else { "not_ready" };
        write!(f, "{overall}")?;

        for (name, component) in &self.components {
            write!(f, ", {name}={}", component.level)?;
        }

        Ok(())
    }
}
