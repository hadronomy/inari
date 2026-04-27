use std::fmt;
use std::marker::PhantomData;
use std::num::NonZeroUsize;
use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde::Serialize;
use serde::ser::SerializeStruct;
use tokio::sync::{OwnedSemaphorePermit, Semaphore, TryAcquireError, watch};

use crate::config::LoadedConfig;
use crate::error::AppError;
use crate::protocol::{DynProtocolModule, ProtocolDescriptor};
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
    protocol: Arc<DynProtocolModule>,
    protocol_budget: Budget<ProtocolExecution>,
    zenoh_rest_request_budget: Budget<ZenohRestRequest>,
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
    pub fn new(
        loaded: LoadedConfig,
        zenoh: ZenohHandle,
        protocol: Arc<DynProtocolModule>,
    ) -> Result<Self, AppError> {
        // TODO: Update the config so that these limits are `NonZeroUsize` directly, so that the invariants are enforced at config load time and we don't have to defensively check them here.
        let protocol_limit = non_zero_limit(
            loaded
                .settings
                .protocol
                .max_concurrent_requests,
            "The protocol execution concurrency limit must be greater than zero.",
        )?;

        let zenoh_rest_requests_limit = non_zero_limit(
            loaded
                .settings
                .http
                .zenoh_rest
                .max_concurrent_requests,
            "The Zenoh REST request concurrency limit must be greater than zero.",
        )?;

        let readiness = Readiness::new(ReadinessSnapshot::new(ReadinessComponents::new(
            HttpReadiness::bootstrapped(),
            ZenohReadiness::from(&zenoh.status_snapshot()),
        )));

        Ok(Self {
            inner: Arc::new(AppStateInner {
                protocol_budget: Budget::new(protocol_limit),
                zenoh_rest_request_budget: Budget::new(zenoh_rest_requests_limit),
                loaded,
                started_at: Utc::now(),
                started_at_instant: Instant::now(),
                readiness,
                zenoh,
                protocol,
            }),
        })
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

    pub async fn acquire_protocol_permit(&self) -> Result<ProtocolPermit, AppError> {
        self.inner
            .protocol_budget
            .acquire()
            .await
    }

    pub fn try_acquire_zenoh_rest_requests_permit(&self) -> Result<ZenohRestQueryPermit, AppError> {
        self.inner
            .zenoh_rest_request_budget
            .try_acquire()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct ConcurrencyLimit(NonZeroUsize);

impl ConcurrencyLimit {
    #[must_use]
    pub const fn get(self) -> usize {
        self.0.get()
    }
}

fn non_zero_limit(value: usize, error_message: &'static str) -> Result<ConcurrencyLimit, AppError> {
    NonZeroUsize::new(value)
        .map(ConcurrencyLimit)
        .ok_or_else(|| AppError::service_unavailable(error_message))
}

mod budget_sealed {
    pub trait Sealed {}
}

pub trait BudgetKind: budget_sealed::Sealed + 'static {
    const EXHAUSTED_MESSAGE: &'static str;
    const CLOSED_MESSAGE: &'static str;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProtocolExecution {}

impl budget_sealed::Sealed for ProtocolExecution {}

impl BudgetKind for ProtocolExecution {
    const EXHAUSTED_MESSAGE: &'static str = "The protocol execution budget is exhausted.";
    const CLOSED_MESSAGE: &'static str = "The protocol execution budget is no longer available.";
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ZenohRestRequest {}

impl budget_sealed::Sealed for ZenohRestRequest {}

impl BudgetKind for ZenohRestRequest {
    const EXHAUSTED_MESSAGE: &'static str =
        "Too many concurrent Zenoh REST queries are already running.";

    const CLOSED_MESSAGE: &'static str = "The Zenoh REST query budget is no longer available.";
}

pub type ProtocolPermit = BudgetPermit<ProtocolExecution>;
pub type ZenohRestQueryPermit = BudgetPermit<ZenohRestRequest>;

#[must_use = "dropping the permit immediately releases the reserved capacity"]
pub struct BudgetPermit<K: BudgetKind> {
    _permit: OwnedSemaphorePermit,
    _kind: PhantomData<fn() -> K>,
}

impl<K: BudgetKind> fmt::Debug for BudgetPermit<K> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BudgetPermit")
            .field("kind", &std::any::type_name::<K>())
            .finish_non_exhaustive()
    }
}

#[derive(Debug)]
struct Budget<K: BudgetKind> {
    semaphore: Arc<Semaphore>,
    _kind: PhantomData<fn() -> K>,
}

impl<K: BudgetKind> Budget<K> {
    fn new(limit: ConcurrencyLimit) -> Self {
        Self { semaphore: Arc::new(Semaphore::new(limit.get())), _kind: PhantomData }
    }

    async fn acquire(&self) -> Result<BudgetPermit<K>, AppError> {
        let permit = self
            .semaphore
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| AppError::service_unavailable(K::CLOSED_MESSAGE))?;

        Ok(BudgetPermit { _permit: permit, _kind: PhantomData })
    }

    fn try_acquire(&self) -> Result<BudgetPermit<K>, AppError> {
        let permit = self
            .semaphore
            .clone()
            .try_acquire_owned()
            .map_err(|error| match error {
                TryAcquireError::NoPermits => AppError::service_unavailable(K::EXHAUSTED_MESSAGE),
                TryAcquireError::Closed => AppError::service_unavailable(K::CLOSED_MESSAGE),
            })?;

        Ok(BudgetPermit { _permit: permit, _kind: PhantomData })
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

mod component_sealed {
    pub trait Sealed {}
}

pub trait Component: component_sealed::Sealed + 'static {
    const NAME: &'static str;
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Http {}

impl component_sealed::Sealed for Http {}

impl Component for Http {
    const NAME: &'static str = "http";
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Zenoh {}

impl component_sealed::Sealed for Zenoh {}

impl Component for Zenoh {
    const NAME: &'static str = "zenoh";
}

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
    zenoh: ZenohReadiness,
}

impl ReadinessComponents {
    #[must_use]
    pub fn new(http: HttpReadiness, zenoh: ZenohReadiness) -> Self {
        Self { http, zenoh }
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
        self.http.counts_as_ready() && self.zenoh.counts_as_ready()
    }

    pub fn iter(&self) -> impl Iterator<Item = ReadinessEntry<'_>> {
        [ReadinessEntry::new(&self.http), ReadinessEntry::new(&self.zenoh)].into_iter()
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
