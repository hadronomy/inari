use std::borrow::Cow;
use std::fmt;
use std::marker::PhantomData;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use tokio::net::TcpListener;
use tokio::task::JoinSet;
use tokio::time::{self, MissedTickBehavior};

use crate::config::LoadedConfig;
use crate::error::{AppError, AppResult};
use crate::http;
use crate::protocol::{NoopProtocolModule, ProtocolModule};
use crate::shutdown::{ShutdownCoordinator, ShutdownReason, wait_for_shutdown_signal};
use crate::state::{AppState, ComponentReadiness, ReadinessSnapshot};
use crate::zenoh::ZenohSupervisor;

#[derive(Debug, Default)]
pub struct MissingConfig;

#[derive(Debug, Clone, Copy, Default)]
pub struct WithConfig;

pub struct ServerBuilder<State> {
    loaded: Option<LoadedConfig>,
    protocol: Arc<dyn ProtocolModule>,
    marker: PhantomData<State>,
}

impl Default for ServerBuilder<MissingConfig> {
    fn default() -> Self {
        Self { loaded: None, protocol: Arc::new(NoopProtocolModule), marker: PhantomData }
    }
}

impl fmt::Debug for ServerBuilder<MissingConfig> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ServerBuilder")
            .field("configured", &false)
            .finish_non_exhaustive()
    }
}

impl fmt::Debug for ServerBuilder<WithConfig> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ServerBuilder")
            .field("configured", &true)
            .field(
                "origin",
                &self
                    .loaded
                    .as_ref()
                    .map(|config| &config.origin),
            )
            .finish_non_exhaustive()
    }
}

impl ServerBuilder<MissingConfig> {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn with_config(self, loaded: LoadedConfig) -> ServerBuilder<WithConfig> {
        ServerBuilder { loaded: Some(loaded), protocol: self.protocol, marker: PhantomData }
    }
}

impl<State> ServerBuilder<State> {
    #[must_use]
    pub fn with_protocol(mut self, protocol: Arc<dyn ProtocolModule>) -> Self {
        self.protocol = protocol;
        self
    }
}

impl ServerBuilder<WithConfig> {
    pub async fn build(self) -> AppResult<ServerApplication> {
        let loaded = self
            .loaded
            .expect("configured builders always carry a loaded configuration");
        let shutdown = ShutdownCoordinator::new(
            loaded
                .settings
                .server
                .shutdown_grace_period,
        );
        let (zenoh_handle, zenoh_supervisor) = ZenohSupervisor::new(loaded.settings.zenoh.clone());
        let state = AppState::new(loaded.clone(), zenoh_handle, self.protocol);
        let router = http::router(&state)?.with_state(state.clone());

        Ok(ServerApplication { loaded, state, router, shutdown, zenoh_supervisor })
    }
}

pub struct ServerApplication {
    loaded: LoadedConfig,
    state: AppState,
    router: Router,
    shutdown: ShutdownCoordinator,
    zenoh_supervisor: ZenohSupervisor,
}

impl fmt::Debug for ServerApplication {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ServerApplication")
            .field("origin", &self.loaded.origin)
            .field("readiness", &self.state.readiness_snapshot())
            .finish_non_exhaustive()
    }
}

impl ServerApplication {
    pub async fn run(self) -> AppResult<()> {
        let bind_address = self.loaded.settings.server.bind;
        let listener = TcpListener::bind(bind_address)
            .await
            .map_err(|source| AppError::Bind { address: bind_address, source })?;
        let local_address = listener.local_addr()?;

        if local_address == bind_address {
            tracing::info!(
                component = "http",
                local_address = %local_address,
                request_timeout = ?self.loaded.settings.server.request_timeout,
                shutdown_grace_period = ?self.loaded.settings.server.shutdown_grace_period,
                "http listener ready"
            );
        } else {
            tracing::info!(
                component = "http",
                bind_address = %bind_address,
                local_address = %local_address,
                request_timeout = ?self.loaded.settings.server.request_timeout,
                shutdown_grace_period = ?self.loaded.settings.server.shutdown_grace_period,
                "http listener ready"
            );
        }

        let mut tasks = JoinSet::new();
        let shutdown = self.shutdown.clone();

        tasks.spawn(named("signal-listener", async move {
            let reason = wait_for_shutdown_signal().await?;
            tracing::info!(task = "signal-listener", %reason, "shutdown requested by signal");
            shutdown.request(reason);
            Ok(())
        }));

        let shutdown = self.shutdown.clone();
        tasks.spawn(named("zenoh-supervisor", self.zenoh_supervisor.run(shutdown)));

        let shutdown = self.shutdown.clone();
        let state = self.state.clone();
        tasks.spawn(named("readiness-sync", async move {
            sync_readiness(state, shutdown).await;
            Ok(())
        }));

        let shutdown = self.shutdown.clone();
        let state = self.state.clone();
        tasks.spawn(named("readiness-monitor", async move {
            monitor_service_readiness(state, shutdown).await;
            Ok(())
        }));

        let shutdown = self.shutdown.clone();
        let router = self
            .router
            .into_make_service_with_connect_info::<std::net::SocketAddr>();
        tasks.spawn(named("http-server", async move {
            let shutdown_signal = async move {
                shutdown.wait_for_shutdown().await;
            };

            axum::serve(listener, router)
                .with_graceful_shutdown(shutdown_signal)
                .await
                .map_err(|source| AppError::Serve { source })
        }));

        let mut failure = None;
        let mut should_drain = false;

        while let Some(result) = tasks.join_next().await {
            match result {
                Ok(NamedTaskResult { name, outcome }) => match outcome {
                    Ok(()) => {
                        tracing::debug!(task = name, "background task completed cleanly");

                        if name == "signal-listener" || name == "http-server" {
                            if name == "http-server" && !self.shutdown.is_requested() {
                                self.shutdown
                                    .request(ShutdownReason::ServerStopped);
                            }

                            should_drain = true;
                            break;
                        }
                    },
                    Err(error) => {
                        let error = error.log_for_server();
                        tracing::error!(task = name, error = %error, "background task failed");
                        if failure.is_none() {
                            failure = Some(error);
                        }
                        if !self.shutdown.is_requested() {
                            self.shutdown
                                .request(ShutdownReason::TaskFailed(Cow::Borrowed(name)));
                        }
                        should_drain = true;
                        break;
                    },
                },
                Err(source) => {
                    let error = AppError::TaskJoin { source }.log_for_server();
                    if failure.is_none() {
                        failure = Some(error);
                    }
                    if !self.shutdown.is_requested() {
                        self.shutdown
                            .request(ShutdownReason::TaskFailed(Cow::Borrowed("background-join")));
                    }
                    should_drain = true;
                    break;
                },
            }
        }

        if should_drain {
            let grace_period = self.shutdown.grace_period();
            if time::timeout(grace_period, drain_remaining(&mut tasks))
                .await
                .is_err()
            {
                tasks.abort_all();
                let _ = drain_remaining(&mut tasks).await;
                if failure.is_none() {
                    failure =
                        Some(AppError::GracefulShutdownTimeout { grace_period }.log_for_server());
                }
            }
        }

        failure.map_or(Ok(()), Err)
    }
}

#[derive(Debug)]
struct NamedTaskResult {
    name: &'static str,
    outcome: AppResult<()>,
}

async fn named<F>(name: &'static str, future: F) -> NamedTaskResult
where
    F: std::future::Future<Output = AppResult<()>> + Send,
{
    NamedTaskResult { name, outcome: future.await }
}

async fn drain_remaining(tasks: &mut JoinSet<NamedTaskResult>) -> AppResult<()> {
    while let Some(result) = tasks.join_next().await {
        match result {
            Ok(task) => match task.outcome {
                Ok(()) => tracing::debug!(task = task.name, "background task drained cleanly"),
                Err(error) => {
                    tracing::error!(task = task.name, error = %error, "task failed while draining");
                    return Err(error);
                },
            },
            Err(source) => return Err(AppError::TaskJoin { source }),
        }
    }

    Ok(())
}

async fn sync_readiness(state: AppState, shutdown: ShutdownCoordinator) {
    let mut zenoh = state.zenoh().subscribe_status();
    state.update_zenoh_readiness(&zenoh.borrow().clone());

    loop {
        tokio::select! {
            _ = shutdown.wait_for_shutdown() => return,
            changed = zenoh.changed() => {
                if changed.is_err() {
                    return;
                }

                state.update_zenoh_readiness(&zenoh.borrow().clone());
            }
        }
    }
}

async fn monitor_service_readiness(state: AppState, shutdown: ShutdownCoordinator) {
    let mut readiness = state.subscribe_readiness();
    let heartbeat_period = Duration::from_secs(30);
    let mut heartbeat =
        time::interval_at(time::Instant::now() + heartbeat_period, heartbeat_period);
    heartbeat.set_missed_tick_behavior(MissedTickBehavior::Skip);

    let initial = state.readiness_snapshot();
    log_readiness_change(&initial);
    let mut last_logged_ready = Some(initial.ready);

    loop {
        tokio::select! {
            _ = shutdown.wait_for_shutdown() => return,
            changed = readiness.changed() => {
                if changed.is_err() {
                    return;
                }

                let snapshot = readiness.borrow().clone();
                if last_logged_ready != Some(snapshot.ready) {
                    log_readiness_change(&snapshot);
                    last_logged_ready = Some(snapshot.ready);
                }
            },
            _ = heartbeat.tick() => {
                let snapshot = state.readiness_snapshot();
                tracing::debug!(
                    component = "service",
                    ready = snapshot.ready,
                    uptime_ms = duration_millis(state.uptime()),
                    "service heartbeat"
                );
            }
        }
    }
}

fn log_readiness_change(snapshot: &ReadinessSnapshot) {
    let http = component(snapshot, "http");
    let zenoh = component(snapshot, "zenoh");

    if snapshot.ready {
        tracing::info!(
            component = "service",
            ready = true,
            http_state = %http.level,
            zenoh_state = %zenoh.level,
            observed_at = %snapshot.observed_at,
            "service became ready"
        );
    } else {
        tracing::info!(
            component = "service",
            ready = false,
            http_state = %http.level,
            zenoh_state = %zenoh.level,
            observed_at = %snapshot.observed_at,
            "service became not ready"
        );
    }
}

fn component<'a>(snapshot: &'a ReadinessSnapshot, name: &str) -> &'a ComponentReadiness {
    snapshot
        .components
        .get(name)
        .unwrap_or_else(|| panic!("missing readiness component `{name}`"))
}

fn duration_millis(duration: Duration) -> u64 {
    duration
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
