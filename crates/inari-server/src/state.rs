use std::fmt;
use std::marker::PhantomData;
use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use inari_gateway::certificate::CertificateIssuerHandle;
use inari_gateway::onboarding::OnboardingService;
use leptos::prelude::LeptosOptions;
use serde::Serialize;
use serde::ser::SerializeStruct;
use tokio::sync::watch;

use crate::config::LoadedConfig;
use crate::coordination::{
    Budget, InariApiPermit, InariApiRequest, ZenohRestQueryPermit, ZenohRestRequest,
};
use crate::error::AppError;
use crate::identity::IdentityRuntime;
use crate::managed_gateway::ManagedGatewayController;
use crate::zenoh::{ZenohConnectionState, ZenohHandle, ZenohStatus};

pub type ReadinessSummary = Arc<str>;

#[derive(Clone)]
pub struct AppState {
    inner: Arc<AppStateInner>,
}

struct AppStateInner {
    loaded: LoadedConfig,
    started_at: DateTime<Utc>,
    started_at_instant: Instant,
    readiness: Readiness,
    zenoh: ZenohHandle,
    managed_gateway: ManagedGatewayController,
    onboarding: Option<OnboardingService>,
    identity: Option<IdentityRuntime>,
    leptos_options: LeptosOptions,
    inari_api_budget: Budget<InariApiRequest>,
    zenoh_rest_request_budget: Budget<ZenohRestRequest>,
}

impl Drop for AppStateInner {
    fn drop(&mut self) {
        self.inari_api_budget.close();
        self.zenoh_rest_request_budget.close();
    }
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
    pub fn new(loaded: LoadedConfig, zenoh: ZenohHandle) -> Self {
        Self::new_with_onboarding(
            loaded,
            zenoh,
            LeptosOptions::builder()
                .output_name("inari-web")
                .build(),
            None,
            None,
            None,
        )
    }

    pub fn new_with_onboarding(
        loaded: LoadedConfig,
        zenoh: ZenohHandle,
        leptos_options: LeptosOptions,
        onboarding: Option<OnboardingService>,
        identity: Option<IdentityRuntime>,
        certificate_issuer: Option<CertificateIssuerHandle>,
    ) -> Self {
        let database_readiness = if onboarding.is_some() || identity.is_some() {
            DatabaseReadiness::ready("PostgreSQL connection pool is available.")
        } else {
            DatabaseReadiness::disabled("Controller persistence is disabled.")
        };
        let identity_readiness = if identity.is_some() {
            IdentityReadiness::ready("OIDC discovery and session storage are available.")
        } else {
            IdentityReadiness::disabled("Organization identity is disabled.")
        };
        let certificate_readiness = if certificate_issuer.is_some() {
            CertificateReadiness::ready("Agent certificate issuer is available.")
        } else {
            CertificateReadiness::disabled("Managed certificate issuance is disabled.")
        };
        let enrollment_readiness = if loaded
            .settings
            .managed_gateway
            .onboarding
            .enabled
        {
            EnrollmentReadiness::ready("Invitation enrollment is available.")
        } else {
            EnrollmentReadiness::disabled("Invitation enrollment is disabled.")
        };
        let readiness = Readiness::new(ReadinessSnapshot::new(ReadinessComponents::new(
            HttpReadiness::bootstrapped(),
            database_readiness,
            identity_readiness,
            certificate_readiness,
            enrollment_readiness,
            ZenohReadiness::from(&zenoh.status_snapshot()),
        )));
        let gateway_repository = onboarding
            .as_ref()
            .map(|onboarding| onboarding.repository().clone());
        let managed_gateway = ManagedGatewayController::new(
            loaded.settings.managed_gateway.clone(),
            loaded.settings.organization.clone(),
            loaded.settings.zenoh.clone(),
            zenoh.clone(),
            gateway_repository,
            certificate_issuer,
        );

        Self {
            inner: Arc::new(AppStateInner {
                inari_api_budget: Budget::new(
                    loaded
                        .settings
                        .http
                        .inari_api
                        .max_concurrent_requests,
                ),
                zenoh_rest_request_budget: Budget::new(
                    loaded
                        .settings
                        .http
                        .zenoh_rest
                        .max_concurrent_requests,
                ),
                loaded,
                started_at: Utc::now(),
                started_at_instant: Instant::now(),
                readiness,
                zenoh,
                managed_gateway,
                onboarding,
                identity,
                leptos_options,
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
    pub fn managed_gateway(&self) -> &ManagedGatewayController {
        &self.inner.managed_gateway
    }

    #[must_use]
    pub fn onboarding(&self) -> Option<&OnboardingService> {
        self.inner.onboarding.as_ref()
    }

    #[must_use]
    pub fn identity(&self) -> Option<&IdentityRuntime> {
        self.inner.identity.as_ref()
    }

    #[must_use]
    pub fn leptos_options(&self) -> &LeptosOptions {
        &self.inner.leptos_options
    }

    #[must_use]
    pub fn readiness_snapshot(&self) -> ReadinessSnapshot {
        self.inner.readiness.snapshot()
    }

    #[must_use]
    pub fn subscribe_readiness(&self) -> watch::Receiver<ReadinessSnapshot> {
        self.inner.readiness.subscribe()
    }

    pub fn update_zenoh_readiness(&self, status: &ZenohStatus) {
        self.inner
            .readiness
            .update_zenoh(status);
    }

    pub fn update_http_readiness(&self, readiness: HttpReadiness) {
        self.inner
            .readiness
            .update_http(readiness);
    }

    pub async fn acquire_inari_api_permit(&self) -> Result<InariApiPermit, AppError> {
        self.inner
            .inari_api_budget
            .acquire()
            .await
    }

    pub fn try_acquire_zenoh_rest_requests_permit(&self) -> Result<ZenohRestQueryPermit, AppError> {
        self.inner
            .zenoh_rest_request_budget
            .try_acquire()
    }
}

impl axum::extract::FromRef<AppState> for LeptosOptions {
    fn from_ref(state: &AppState) -> Self {
        state.inner.leptos_options.clone()
    }
}

#[derive(Debug)]
struct Readiness {
    sender: watch::Sender<ReadinessSnapshot>,
}

impl Readiness {
    fn new(snapshot: ReadinessSnapshot) -> Self {
        let (sender, _) = watch::channel(snapshot);
        Self { sender }
    }

    fn snapshot(&self) -> ReadinessSnapshot {
        self.sender.borrow().clone()
    }

    fn subscribe(&self) -> watch::Receiver<ReadinessSnapshot> {
        self.sender.subscribe()
    }

    fn update_http(&self, readiness: HttpReadiness) {
        self.sender.send_modify(|snapshot| {
            snapshot.set_component(readiness);
        });
    }

    fn update_zenoh(&self, status: &ZenohStatus) {
        self.sender.send_modify(|snapshot| {
            snapshot.set_component(ZenohReadiness::from(status));
        });
    }
}

mod sealed {
    pub trait Sealed {}
}

pub trait Component: sealed::Sealed + 'static {
    const NAME: &'static str;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Http {}

impl sealed::Sealed for Http {}

impl Component for Http {
    const NAME: &'static str = "http";
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Zenoh {}

impl sealed::Sealed for Zenoh {}

impl Component for Zenoh {
    const NAME: &'static str = "zenoh";
}

macro_rules! readiness_component {
    ($marker:ident, $alias:ident, $name:literal) => {
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub enum $marker {}

        impl sealed::Sealed for $marker {}

        impl Component for $marker {
            const NAME: &'static str = $name;
        }

        pub type $alias = ComponentReadiness<$marker>;
    };
}

readiness_component!(Database, DatabaseReadiness, "database");
readiness_component!(Identity, IdentityReadiness, "identity");
readiness_component!(Certificate, CertificateReadiness, "certificate");
readiness_component!(Enrollment, EnrollmentReadiness, "enrollment");

pub type HttpReadiness = ComponentReadiness<Http>;
pub type ZenohReadiness = ComponentReadiness<Zenoh>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReadinessLevel {
    Ready,
    Starting,
    Degraded,
    Disabled,
}

impl ReadinessLevel {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Starting => "starting",
            Self::Degraded => "degraded",
            Self::Disabled => "disabled",
        }
    }

    #[must_use]
    pub const fn counts_as_ready(self) -> bool {
        // A disabled optional integration does not block service readiness.
        matches!(self, Self::Ready | Self::Disabled)
    }
}

impl fmt::Display for ReadinessLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadinessStatus {
    level: ReadinessLevel,
    summary: ReadinessSummary,
}

impl ReadinessStatus {
    #[must_use]
    pub fn ready(summary: impl Into<ReadinessSummary>) -> Self {
        Self::new(ReadinessLevel::Ready, summary)
    }

    #[must_use]
    pub fn starting(summary: impl Into<ReadinessSummary>) -> Self {
        Self::new(ReadinessLevel::Starting, summary)
    }

    #[must_use]
    pub fn degraded(summary: impl Into<ReadinessSummary>) -> Self {
        Self::new(ReadinessLevel::Degraded, summary)
    }

    #[must_use]
    pub fn disabled(summary: impl Into<ReadinessSummary>) -> Self {
        Self::new(ReadinessLevel::Disabled, summary)
    }

    fn new(level: ReadinessLevel, summary: impl Into<ReadinessSummary>) -> Self {
        Self { level, summary: summary.into() }
    }

    #[must_use]
    pub const fn level(&self) -> ReadinessLevel {
        self.level
    }

    #[must_use]
    pub fn summary(&self) -> &str {
        self.summary.as_ref()
    }

    #[must_use]
    pub const fn counts_as_ready(&self) -> bool {
        self.level.counts_as_ready()
    }
}

impl Serialize for ReadinessStatus {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        let mut state = serializer.serialize_struct("ReadinessStatus", 2)?;
        state.serialize_field("level", &self.level)?;
        state.serialize_field("summary", self.summary())?;
        state.end()
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComponentReadiness<C: Component> {
    status: ReadinessStatus,
    component: PhantomData<fn() -> C>,
}

impl<C: Component> ComponentReadiness<C> {
    #[must_use]
    pub fn ready(summary: impl Into<ReadinessSummary>) -> Self {
        Self::from_status(ReadinessStatus::ready(summary))
    }

    #[must_use]
    pub fn starting(summary: impl Into<ReadinessSummary>) -> Self {
        Self::from_status(ReadinessStatus::starting(summary))
    }

    #[must_use]
    pub fn degraded(summary: impl Into<ReadinessSummary>) -> Self {
        Self::from_status(ReadinessStatus::degraded(summary))
    }

    #[must_use]
    pub fn disabled(summary: impl Into<ReadinessSummary>) -> Self {
        Self::from_status(ReadinessStatus::disabled(summary))
    }

    fn from_status(status: ReadinessStatus) -> Self {
        Self { status, component: PhantomData }
    }

    #[must_use]
    pub const fn component_name(&self) -> &'static str {
        C::NAME
    }

    #[must_use]
    pub const fn level(&self) -> ReadinessLevel {
        self.status.level()
    }

    #[must_use]
    pub const fn counts_as_ready(&self) -> bool {
        self.status.counts_as_ready()
    }

    #[must_use]
    pub fn summary(&self) -> &str {
        self.status.summary()
    }

    #[must_use]
    pub const fn status(&self) -> &ReadinessStatus {
        &self.status
    }
}

impl<C: Component> Serialize for ComponentReadiness<C> {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        self.status.serialize(serializer)
    }
}

impl ComponentReadiness<Http> {
    #[must_use]
    pub fn bootstrapped() -> Self {
        Self::ready("HTTP server bootstrap completed.")
    }

    #[must_use]
    pub fn listening() -> Self {
        Self::ready("HTTP listener is accepting connections.")
    }
}

impl From<&ZenohStatus> for ComponentReadiness<Zenoh> {
    fn from(status: &ZenohStatus) -> Self {
        match status.state {
            ZenohConnectionState::Disabled => Self::disabled("Zenoh integration is disabled."),

            ZenohConnectionState::Starting | ZenohConnectionState::Reconnecting => Self::starting(
                status
                    .message
                    .as_deref()
                    .unwrap_or("Zenoh session is still starting."),
            ),

            ZenohConnectionState::Connected => Self::ready("Zenoh session is connected."),

            ZenohConnectionState::Degraded => Self::degraded(
                status
                    .message
                    .as_deref()
                    .unwrap_or("Zenoh session is unavailable."),
            ),

            ZenohConnectionState::ShuttingDown => Self::degraded("Zenoh session is shutting down."),
        }
    }
}

impl From<ZenohStatus> for ComponentReadiness<Zenoh> {
    fn from(status: ZenohStatus) -> Self {
        Self::from(&status)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ReadinessComponents {
    http: HttpReadiness,
    database: DatabaseReadiness,
    identity: IdentityReadiness,
    certificate: CertificateReadiness,
    enrollment: EnrollmentReadiness,
    zenoh: ZenohReadiness,
}

impl ReadinessComponents {
    #[must_use]
    pub fn new(
        http: HttpReadiness,
        database: DatabaseReadiness,
        identity: IdentityReadiness,
        certificate: CertificateReadiness,
        enrollment: EnrollmentReadiness,
        zenoh: ZenohReadiness,
    ) -> Self {
        Self { http, database, identity, certificate, enrollment, zenoh }
    }

    #[must_use]
    pub fn http(&self) -> &HttpReadiness {
        &self.http
    }

    #[must_use]
    pub fn zenoh(&self) -> &ZenohReadiness {
        &self.zenoh
    }

    #[must_use]
    pub fn component<C>(&self) -> &ComponentReadiness<C>
    where
        C: Component,
        Self: ComponentSlot<C>,
    {
        <Self as ComponentSlot<C>>::get(self)
    }

    pub fn set_component<C>(&mut self, readiness: ComponentReadiness<C>)
    where
        C: Component,
        Self: ComponentSlot<C>,
    {
        <Self as ComponentSlot<C>>::set(self, readiness);
    }

    #[must_use]
    pub fn is_ready(&self) -> bool {
        self.iter()
            .all(ReadinessEntry::counts_as_ready)
    }

    pub fn iter(&self) -> impl Iterator<Item = ReadinessEntry<'_>> {
        [
            ReadinessEntry::new(&self.http),
            ReadinessEntry::new(&self.database),
            ReadinessEntry::new(&self.identity),
            ReadinessEntry::new(&self.certificate),
            ReadinessEntry::new(&self.enrollment),
            ReadinessEntry::new(&self.zenoh),
        ]
        .into_iter()
    }
}

pub trait ComponentSlot<C: Component> {
    fn get(&self) -> &ComponentReadiness<C>;
    fn set(&mut self, readiness: ComponentReadiness<C>);
}

impl ComponentSlot<Http> for ReadinessComponents {
    fn get(&self) -> &HttpReadiness {
        &self.http
    }

    fn set(&mut self, readiness: HttpReadiness) {
        self.http = readiness;
    }
}

impl ComponentSlot<Zenoh> for ReadinessComponents {
    fn get(&self) -> &ZenohReadiness {
        &self.zenoh
    }

    fn set(&mut self, readiness: ZenohReadiness) {
        self.zenoh = readiness;
    }
}

macro_rules! component_slot {
    ($marker:ident, $field:ident, $readiness:ident) => {
        impl ComponentSlot<$marker> for ReadinessComponents {
            fn get(&self) -> &$readiness {
                &self.$field
            }

            fn set(&mut self, readiness: $readiness) {
                self.$field = readiness;
            }
        }
    };
}

component_slot!(Database, database, DatabaseReadiness);
component_slot!(Identity, identity, IdentityReadiness);
component_slot!(Certificate, certificate, CertificateReadiness);
component_slot!(Enrollment, enrollment, EnrollmentReadiness);

#[derive(Debug, Clone, Copy)]
pub struct ReadinessEntry<'a> {
    name: &'static str,
    level: ReadinessLevel,
    summary: &'a str,
    counts_as_ready: bool,
}

impl<'a> ReadinessEntry<'a> {
    fn new<C: Component>(readiness: &'a ComponentReadiness<C>) -> Self {
        Self {
            name: C::NAME,
            level: readiness.level(),
            summary: readiness.summary(),
            counts_as_ready: readiness.counts_as_ready(),
        }
    }

    #[must_use]
    pub const fn name(self) -> &'static str {
        self.name
    }

    #[must_use]
    pub const fn level(self) -> ReadinessLevel {
        self.level
    }

    #[must_use]
    pub const fn summary(self) -> &'a str {
        self.summary
    }

    #[must_use]
    pub const fn counts_as_ready(self) -> bool {
        self.counts_as_ready
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReadinessSnapshot {
    data: ReadinessSnapshotData,
}

impl ReadinessSnapshot {
    #[must_use]
    pub fn new(components: ReadinessComponents) -> Self {
        Self { data: ReadinessSnapshotData { observed_at: Utc::now(), components } }
    }

    #[must_use]
    pub fn is_ready(&self) -> bool {
        self.data.components.is_ready()
    }

    #[must_use]
    pub fn observed_at(&self) -> DateTime<Utc> {
        self.data.observed_at
    }

    #[must_use]
    pub fn components(&self) -> &ReadinessComponents {
        &self.data.components
    }

    #[must_use]
    pub fn component<C>(&self) -> &ComponentReadiness<C>
    where
        C: Component,
        ReadinessComponents: ComponentSlot<C>,
    {
        self.data.components.component::<C>()
    }

    pub fn set_component<C>(&mut self, readiness: ComponentReadiness<C>)
    where
        C: Component,
        ReadinessComponents: ComponentSlot<C>,
    {
        self.data
            .components
            .set_component(readiness);
        self.data.observed_at = Utc::now();
    }
}

impl Serialize for ReadinessSnapshot {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        #[derive(Serialize)]
        struct Wire<'a> {
            ready: bool,

            #[serde(flatten)]
            data: &'a ReadinessSnapshotData,
        }

        Wire { ready: self.is_ready(), data: &self.data }.serialize(serializer)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ReadinessSnapshotData {
    observed_at: DateTime<Utc>,
    components: ReadinessComponents,
}

impl fmt::Display for ReadinessSnapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let overall = if self.is_ready() { "ready" } else { "not_ready" };

        write!(f, "{overall}")?;

        for component in self.data.components.iter() {
            write!(f, ", {}={}", component.name(), component.level())?;
        }

        Ok(())
    }
}
