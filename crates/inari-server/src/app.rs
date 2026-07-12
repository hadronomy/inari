use std::borrow::Cow;
use std::fmt;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use axum::Router;
use axum::extract::Request;
use inari_gateway::onboarding::{CertificateMode, OnboardingConfig, OnboardingService};
use leptos::prelude::get_configuration;
use secrecy::SecretString;
use tokio::net::TcpListener;
use tokio::task::JoinSet;
use tokio::time::{self, MissedTickBehavior};
use tower::ServiceBuilder;
use tower_http::normalize_path::NormalizePathLayer;
use tower_sessions::session_store::ExpiredDeletion;
use url::Url;

use crate::config::LoadedConfig;
use crate::database::ControllerDatabase;
use crate::error::{AppError, AppResult};
use crate::http;
use crate::identity::{IdentityRuntime, IdentityService};
use crate::managed_gateway::StepCaIssuer;
use crate::shutdown::{ShutdownCoordinator, ShutdownReason, wait_for_shutdown_signal};
use crate::state::{AppState, Http, HttpReadiness, ReadinessSnapshot, Zenoh};
use crate::zenoh::ZenohSupervisor;

#[derive(Debug, Clone, Copy, Default)]
pub struct MissingConfig;

#[derive(Debug, Clone)]
pub struct WithConfig {
    loaded: LoadedConfig,
}

pub struct ServerBuilder<State = MissingConfig> {
    state: State,
}

impl Default for ServerBuilder<MissingConfig> {
    fn default() -> Self {
        Self { state: MissingConfig }
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
            .field("origin", &self.state.loaded.origin)
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
        ServerBuilder { state: WithConfig { loaded } }
    }
}

impl ServerBuilder<WithConfig> {
    pub async fn build(self) -> AppResult<ServerApplication> {
        let loaded = self.state.loaded;

        let shutdown = ShutdownCoordinator::new(
            loaded
                .settings
                .server
                .shutdown_grace_period,
        );

        let (zenoh_handle, zenoh_supervisor) = ZenohSupervisor::new(loaded.settings.zenoh.clone());

        let leptos_options = get_configuration(leptos_manifest())
            .map_err(|source| {
                AppError::internal(
                    "leptos_configuration",
                    "Failed to load the Leptos runtime configuration.",
                )
                .with_source(source)
            })?
            .leptos_options;
        let database = initialize_database(&loaded).await?;
        let onboarding = initialize_onboarding(&loaded, database.as_ref()).await?;
        let identity = initialize_identity(&loaded, database.as_ref()).await?;
        let certificate_issuer = initialize_certificate_issuer(&loaded).await?;
        let state = AppState::new_with_onboarding(
            loaded,
            zenoh_handle,
            leptos_options,
            onboarding,
            identity,
            certificate_issuer,
        );
        let router = http::router(&state)?.with_state(state.clone());

        Ok(ServerApplication { state, router, shutdown, zenoh_supervisor })
    }
}

async fn initialize_certificate_issuer(
    loaded: &LoadedConfig,
) -> AppResult<Option<inari_gateway::certificate::CertificateIssuerHandle>> {
    use crate::config::ManagedGatewayCertificateMode;

    if !loaded.settings.managed_gateway.enabled
        || loaded
            .settings
            .managed_gateway
            .certificate
            .mode
            == ManagedGatewayCertificateMode::None
    {
        return Ok(None);
    }
    let issuer = StepCaIssuer::load(
        &loaded
            .settings
            .managed_gateway
            .certificate,
    )
    .await?;
    Ok(Some(Arc::new(issuer)))
}

async fn initialize_identity(
    loaded: &LoadedConfig,
    database: Option<&ControllerDatabase>,
) -> AppResult<Option<IdentityRuntime>> {
    let config = &loaded.settings.identity.oidc;
    if !config.enabled {
        return Ok(None);
    }
    let public_url = loaded
        .settings
        .server
        .public_url
        .as_ref()
        .ok_or_else(|| {
            AppError::internal("oidc_configuration", "OIDC requires server.public_url.")
        })?;
    let client_secret = match config.client_secret_file.as_ref() {
        Some(path) => Some(SecretString::from(
            tokio::fs::read_to_string(path)
                .await
                .map_err(|source| {
                    AppError::internal(
                        "oidc_client_secret",
                        "The OIDC client secret could not be read.",
                    )
                    .with_source(source)
                })?
                .trim()
                .to_owned(),
        )),
        None => None,
    };
    let database = database.ok_or_else(|| {
        AppError::internal("oidc_database", "OIDC requires the managed controller database.")
    })?;
    let sessions = tower_sessions_sqlx_store::PostgresStore::new(database.pool().clone());
    let service = IdentityService::discover(config, public_url, client_secret).await?;
    Ok(Some(IdentityRuntime::new(service, sessions)))
}

fn leptos_manifest() -> Option<&'static str> {
    let configured_by_cargo_leptos = option_env!("LEPTOS_OUTPUT_NAME").is_some()
        || std::env::var_os("LEPTOS_OUTPUT_NAME").is_some();
    (!configured_by_cargo_leptos).then_some("Cargo.toml")
}

async fn initialize_database(loaded: &LoadedConfig) -> AppResult<Option<ControllerDatabase>> {
    if !loaded.settings.managed_gateway.enabled && !loaded.settings.identity.oidc.enabled {
        return Ok(None);
    }
    let database = ControllerDatabase::connect(&loaded.settings.database).await?;
    if loaded
        .settings
        .database
        .migrate_on_startup
    {
        let report = database.migrate().await?;
        tracing::info!(
            component = "database",
            applied = report.applied.len(),
            pending = report.pending.len(),
            lock_wait_ms = u64::try_from(report.lock_wait.as_millis()).unwrap_or(u64::MAX),
            "controller database migrations applied"
        );
    } else {
        database.ensure_current().await?;
    }
    Ok(Some(database))
}

async fn initialize_onboarding(
    loaded: &LoadedConfig,
    database: Option<&ControllerDatabase>,
) -> AppResult<Option<OnboardingService>> {
    let config = &loaded.settings.managed_gateway;
    if !config.enabled {
        return Ok(None);
    }
    let public_base_url = config
        .onboarding
        .public_base_url
        .as_deref()
        .map(str::parse::<Url>)
        .transpose()
        .map_err(|source| {
            AppError::internal(
                "managed_gateway_public_url",
                "Managed onboarding public_base_url is invalid.",
            )
            .with_source(source)
        })?;
    let database = database.ok_or_else(|| {
        AppError::internal(
            "managed_gateway_database",
            "Managed onboarding requires the controller database.",
        )
    })?;
    OnboardingService::initialize(
        OnboardingConfig {
            organization_id: loaded.settings.organization.id.clone(),
            organization_name: loaded
                .settings
                .organization
                .name
                .clone(),
            default_site_id: loaded
                .settings
                .organization
                .default_site_id
                .clone(),
            default_site_name: loaded
                .settings
                .organization
                .default_site_name
                .clone(),
            enabled: config.onboarding.enabled,
            public_base_url,
            controller_name: config.controller_name.clone(),
            controller_instance_id: config.controller_instance_id.clone(),
            invitation_ttl: config.onboarding.invite_ttl,
            supported_protocol_versions: config
                .supported_protocol_versions
                .clone(),
            certificate_mode: match config.certificate.mode {
                crate::config::ManagedGatewayCertificateMode::None => CertificateMode::None,
                crate::config::ManagedGatewayCertificateMode::StepCa => CertificateMode::StepCa,
            },
            requires_mutual_tls_after_issuance: config
                .certificate
                .requires_mutual_tls_after_issuance,
        },
        inari_gateway::GatewayRepository::new(database.sea_orm().clone()),
    )
    .await
    .map(Some)
    .map_err(AppError::from)
}

pub struct ServerApplication {
    state: AppState,
    router: Router,
    shutdown: ShutdownCoordinator,
    zenoh_supervisor: ZenohSupervisor,
}

impl fmt::Debug for ServerApplication {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ServerApplication")
            .field("origin", &self.state.loaded_config().origin)
            .field("readiness", &self.state.readiness_snapshot())
            .finish_non_exhaustive()
    }
}

impl ServerApplication {
    pub async fn run(self) -> AppResult<()> {
        let ServerApplication { state, router, shutdown, zenoh_supervisor } = self;

        let server_settings = &state.loaded_config().settings.server;
        let bind_address = server_settings.bind;
        let request_timeout = server_settings.request_timeout;
        let shutdown_grace_period = server_settings.shutdown_grace_period;

        let listener = TcpListener::bind(bind_address)
            .await
            .map_err(|source| AppError::Bind { address: bind_address, source })?;

        let local_address = listener.local_addr()?;

        state.update_http_readiness(HttpReadiness::listening());

        log_http_listener_ready(
            bind_address,
            local_address,
            request_timeout,
            shutdown_grace_period,
        );

        let mut tasks = JoinSet::new();

        let signal_shutdown = shutdown.clone();
        tasks.spawn(named(TaskName::SignalListener, async move {
            let reason = wait_for_shutdown_signal().await?;

            tracing::info!(
                task = %TaskName::SignalListener,
                %reason,
                "shutdown requested by signal"
            );

            signal_shutdown.request(reason);

            Ok(())
        }));

        tasks.spawn(named(TaskName::ZenohSupervisor, zenoh_supervisor.run(shutdown.clone())));

        if let Some(identity) = state.identity() {
            let sessions = identity.sessions().clone();
            let session_shutdown = shutdown.clone();
            tasks.spawn(named(TaskName::SessionCleanup, async move {
                tokio::select! {
                    result = sessions.continuously_delete_expired(Duration::from_secs(300)) => {
                        result.map_err(|source| {
                            AppError::internal(
                                "session_cleanup",
                                "The expired session cleanup task failed.",
                            )
                            .with_source(source)
                        })
                    }
                    _ = session_shutdown.wait_for_shutdown() => Ok(()),
                }
            }));
        }

        let managed_gateway = state.managed_gateway().clone();
        tasks.spawn(named(
            TaskName::ManagedGatewayDataPlane,
            managed_gateway.run_data_plane(shutdown.clone()),
        ));

        let readiness_sync_state = state.clone();
        let readiness_sync_shutdown = shutdown.clone();
        tasks.spawn(named(TaskName::ReadinessSync, async move {
            sync_readiness(readiness_sync_state, readiness_sync_shutdown).await;
            Ok(())
        }));

        let readiness_monitor_state = state.clone();
        let readiness_monitor_shutdown = shutdown.clone();
        tasks.spawn(named(TaskName::ReadinessMonitor, async move {
            monitor_service_readiness(readiness_monitor_state, readiness_monitor_shutdown).await;
            Ok(())
        }));

        let service = ServiceBuilder::new()
            .layer(NormalizePathLayer::trim_trailing_slash())
            .service(router);

        let service =
            axum::ServiceExt::<Request>::into_make_service_with_connect_info::<SocketAddr>(service);

        let http_shutdown = shutdown.clone();

        tasks.spawn(named(TaskName::HttpServer, async move {
            let shutdown_signal = async move {
                http_shutdown.wait_for_shutdown().await;
            };

            axum::serve(listener, service)
                .with_graceful_shutdown(shutdown_signal)
                .await
                .map_err(|source| AppError::Serve { source })
        }));

        supervise_tasks(&mut tasks, &shutdown).await
    }
}

fn log_http_listener_ready(
    bind_address: SocketAddr,
    local_address: SocketAddr,
    request_timeout: Duration,
    shutdown_grace_period: Duration,
) {
    if local_address == bind_address {
        tracing::info!(
            component = "http",
            local_address = %local_address,
            request_timeout = ?request_timeout,
            shutdown_grace_period = ?shutdown_grace_period,
            "http listener ready"
        );
    } else {
        tracing::info!(
            component = "http",
            bind_address = %bind_address,
            local_address = %local_address,
            request_timeout = ?request_timeout,
            shutdown_grace_period = ?shutdown_grace_period,
            "http listener ready"
        );
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum TaskName {
    SignalListener,
    ZenohSupervisor,
    SessionCleanup,
    ManagedGatewayDataPlane,
    ReadinessSync,
    ReadinessMonitor,
    HttpServer,
}

impl TaskName {
    const fn as_str(self) -> &'static str {
        match self {
            Self::SignalListener => "signal-listener",
            Self::ZenohSupervisor => "zenoh-supervisor",
            Self::SessionCleanup => "session-cleanup",
            Self::ManagedGatewayDataPlane => "managed-gateway-data-plane",
            Self::ReadinessSync => "readiness-sync",
            Self::ReadinessMonitor => "readiness-monitor",
            Self::HttpServer => "http-server",
        }
    }

    const fn clean_exit_policy(self) -> CleanExitPolicy {
        match self {
            Self::SignalListener | Self::HttpServer => CleanExitPolicy::ShutdownApplication,
            Self::ZenohSupervisor
            | Self::SessionCleanup
            | Self::ManagedGatewayDataPlane
            | Self::ReadinessSync
            | Self::ReadinessMonitor => CleanExitPolicy::KeepRunning,
        }
    }
}

impl fmt::Display for TaskName {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CleanExitPolicy {
    ShutdownApplication,
    KeepRunning,
}

#[derive(Debug)]
struct NamedTaskResult {
    name: TaskName,
    outcome: AppResult<()>,
}

async fn named<F>(name: TaskName, future: F) -> NamedTaskResult
where
    F: std::future::Future<Output = AppResult<()>> + Send + 'static,
{
    NamedTaskResult { name, outcome: future.await }
}

async fn supervise_tasks(
    tasks: &mut JoinSet<NamedTaskResult>,
    shutdown: &ShutdownCoordinator,
) -> AppResult<()> {
    let mut failure = None;
    let should_drain = loop {
        let Some(result) = tasks.join_next().await else {
            break false;
        };

        let NamedTaskResult { name, outcome } = match result {
            Ok(result) => result,
            Err(source) => {
                let error = AppError::TaskJoin { source }.log_for_server();

                tracing::error!(
                    error = %error,
                    "background task join failed"
                );

                failure.get_or_insert(error);

                if !shutdown.is_requested() {
                    shutdown.request(ShutdownReason::TaskFailed(Cow::Borrowed("background-join")));
                }

                break true;
            },
        };

        match outcome {
            Ok(()) => {
                tracing::debug!(
                    task = %name,
                    "background task completed cleanly"
                );

                if name.clean_exit_policy() == CleanExitPolicy::ShutdownApplication {
                    if name == TaskName::HttpServer && !shutdown.is_requested() {
                        shutdown.request(ShutdownReason::ServerStopped);
                    }

                    break true;
                }
            },

            Err(error) => {
                let error = error.log_for_server();

                tracing::error!(
                    task = %name,
                    error = %error,
                    "background task failed"
                );

                failure.get_or_insert(error);

                if !shutdown.is_requested() {
                    shutdown.request(ShutdownReason::TaskFailed(Cow::Borrowed(name.as_str())));
                }

                break true;
            },
        }
    };

    if should_drain && let Err(error) = drain_with_timeout(tasks, shutdown).await {
        let error = error.log_for_server();

        if failure.is_none() {
            failure = Some(error);
        }
    }

    failure.map_or(Ok(()), Err)
}

async fn drain_with_timeout(
    tasks: &mut JoinSet<NamedTaskResult>,
    shutdown: &ShutdownCoordinator,
) -> AppResult<()> {
    let grace_period = shutdown.grace_period();

    match time::timeout(grace_period, drain_remaining(tasks)).await {
        Ok(result) => result,
        Err(_) => {
            tasks.abort_all();

            let _ = drain_remaining(tasks).await;

            Err(AppError::GracefulShutdownTimeout { grace_period })
        },
    }
}

async fn drain_remaining(tasks: &mut JoinSet<NamedTaskResult>) -> AppResult<()> {
    while let Some(result) = tasks.join_next().await {
        match result {
            Ok(task) => match task.outcome {
                Ok(()) => {
                    tracing::debug!(
                        task = %task.name,
                        "background task drained cleanly"
                    );
                },
                Err(error) => {
                    tracing::error!(
                        task = %task.name,
                        error = %error,
                        "task failed while draining"
                    );

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

    let mut last_logged = initial.to_string();

    loop {
        tokio::select! {
            _ = shutdown.wait_for_shutdown() => return,

            changed = readiness.changed() => {
                if changed.is_err() {
                    return;
                }

                let snapshot = readiness.borrow().clone();
                let current = snapshot.to_string();

                if last_logged != current {
                    log_readiness_change(&snapshot);
                    last_logged = current;
                }
            }

            _ = heartbeat.tick() => {
                let snapshot = state.readiness_snapshot();

                tracing::debug!(
                    component = "service",
                    ready = snapshot.is_ready(),
                    readiness = %snapshot,
                    uptime_ms = duration_millis(state.uptime()),
                    "service heartbeat"
                );
            }
        }
    }
}

#[inline(always)]
fn log_readiness_change(snapshot: &ReadinessSnapshot) {
    let http = snapshot.component::<Http>();
    let zenoh = snapshot.component::<Zenoh>();

    tracing::info!(
        component = "service",
        ready = snapshot.is_ready(),
        http = %http.level(),
        zenoh = %zenoh.level(),
        observed_at = %snapshot.observed_at().to_rfc3339(),
        "readiness changed"
    );
}

fn duration_millis(duration: Duration) -> u64 {
    duration
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
