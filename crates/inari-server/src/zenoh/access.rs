use std::fmt;
use std::future::Future;
use std::num::NonZeroUsize;
use std::time::Duration;

use bytes::Bytes;
use zenoh::Session;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::config::ZenohId;
use zenoh::handlers::{FifoChannel, FifoChannelHandler};
use zenoh::pubsub::Subscriber;
use zenoh::query::{QueryConsolidation, Reply, Selector};
use zenoh::sample::Sample;

use super::KeyExpression;
use crate::error::{AppError, AppResult};

#[derive(Clone)]
pub(crate) struct CurrentSession {
    session: Session,
    generation: Generation,
}

impl CurrentSession {
    pub(crate) fn new(session: Session, generation: Generation) -> Self {
        Self { session, generation }
    }

    pub(crate) fn session(&self) -> &Session {
        &self.session
    }

    pub(crate) fn generation(&self) -> Generation {
        self.generation
    }

    pub(crate) fn zid(&self) -> ZenohId {
        self.session.zid()
    }
}

impl fmt::Debug for CurrentSession {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SessionLease")
            .field("zid", &self.session.zid())
            .field("generation", &self.generation)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub(crate) struct Generation(u64);

impl Generation {
    pub(crate) const ZERO: Self = Self(0);

    pub(crate) fn next(self) -> Self {
        Self(self.0.saturating_add(1))
    }
}

impl From<Generation> for u64 {
    fn from(generation: Generation) -> Self {
        generation.0
    }
}

impl From<Generation> for String {
    fn from(generation: Generation) -> Self {
        generation.0.to_string()
    }
}

// TODO: See if theses great new types can be reused in other parts of the codebase.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(crate) struct ChannelCapacity(NonZeroUsize);

impl ChannelCapacity {
    pub(crate) const fn new(capacity: NonZeroUsize) -> Self {
        Self(capacity)
    }

    pub(crate) const fn get(self) -> usize {
        self.0.get()
    }
}

impl TryFrom<usize> for ChannelCapacity {
    type Error = AppError;

    fn try_from(value: usize) -> Result<Self, Self::Error> {
        NonZeroUsize::new(value)
            .map(Self)
            .ok_or_else(|| {
                AppError::service_unavailable("Zenoh channel capacity must be greater than zero.")
            })
    }
}

impl From<NonZeroUsize> for ChannelCapacity {
    fn from(value: NonZeroUsize) -> Self {
        Self::new(value)
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ZenohQueryRequest {
    selector: Selector<'static>,
    payload: Option<ZenohRequestPayload>,
    timeout: Duration,
    consolidation: QueryConsolidation,
}

impl ZenohQueryRequest {
    pub(crate) fn new(
        selector: Selector<'static>,
        timeout: Duration,
        consolidation: QueryConsolidation,
    ) -> Self {
        Self { selector, payload: None, timeout, consolidation }
    }

    pub(crate) const fn timeout(&self) -> Duration {
        self.timeout
    }

    pub(crate) fn with_payload(mut self, payload: ZenohRequestPayload) -> Self {
        self.payload = Some(payload);
        self
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ZenohRequestPayload {
    body: Bytes,
    encoding: Encoding,
    attachment: Option<Bytes>,
}

impl ZenohRequestPayload {
    pub(crate) fn new(body: impl Into<Bytes>, encoding: Encoding) -> Self {
        Self { body: body.into(), encoding, attachment: None }
    }

    pub(crate) fn with_attachment(mut self, attachment: impl Into<Bytes>) -> Self {
        self.attachment = Some(attachment.into());
        self
    }
}

#[derive(Debug)]
pub(crate) struct ZenohSubscription {
    subscriber: Subscriber<FifoChannelHandler<Sample>>,
}

impl ZenohSubscription {
    pub(crate) fn new(subscriber: Subscriber<FifoChannelHandler<Sample>>) -> Self {
        Self { subscriber }
    }

    pub(crate) async fn recv_async(&self) -> AppResult<Sample> {
        self.subscriber
            .recv_async()
            .await
            .map_err(|source| {
                tracing::debug!(error = %source, "Zenoh subscription stream closed");
                AppError::service_unavailable("Zenoh subscription stream is no longer available.")
            })
    }
}

pub(crate) async fn execute_query_collect(
    session: &CurrentSession,
    request: ZenohQueryRequest,
) -> AppResult<Vec<Reply>> {
    let deadline = Deadline::after(request.timeout());
    let replies = issue_query(session, request, deadline).await?;

    replies.collect(deadline).await
}

pub(crate) async fn execute_query_first(
    session: &CurrentSession,
    request: ZenohQueryRequest,
) -> AppResult<Option<Reply>> {
    let deadline = Deadline::after(request.timeout());
    let replies = issue_query(session, request, deadline).await?;

    replies.first(deadline).await
}

pub(crate) async fn execute_liveliness_get_collect(
    session: &CurrentSession,
    key: &KeyExpression,
    timeout: Duration,
) -> AppResult<Vec<Reply>> {
    let deadline = Deadline::after(timeout);
    let replies = issue_liveliness_get(session, key, deadline).await?;

    replies.collect(deadline).await
}

pub(crate) async fn execute_liveliness_get_first(
    session: &CurrentSession,
    key: &KeyExpression,
    timeout: Duration,
) -> AppResult<Option<Reply>> {
    let deadline = Deadline::after(timeout);
    let replies = issue_liveliness_get(session, key, deadline).await?;

    replies.first(deadline).await
}

async fn issue_query(
    session: &CurrentSession,
    request: ZenohQueryRequest,
    deadline: Deadline,
) -> AppResult<Replies> {
    let ZenohQueryRequest { selector, payload, timeout: _, consolidation } = request;

    let mut query = session
        .session()
        .get(selector)
        .consolidation(consolidation)
        .timeout(deadline.remaining()?);

    if let Some(payload) = payload {
        let ZenohRequestPayload { body, encoding, attachment } = payload;

        query = query
            .payload(ZBytes::from(body))
            .encoding(encoding)
            .attachment(attachment.map(ZBytes::from));
    }

    query
        .await
        .map(Replies::new)
        .map_zenoh_error("Zenoh query failed", "Zenoh query failed.")
}

async fn issue_liveliness_get(
    session: &CurrentSession,
    key: &KeyExpression,
    deadline: Deadline,
) -> AppResult<Replies> {
    let error_message = "Zenoh liveliness query failed.";
    session
        .session()
        .liveliness()
        .get(key.clone())
        .timeout(deadline.remaining()?)
        .await
        .map(Replies::new)
        .map_zenoh_error(error_message, error_message)
}

pub(crate) async fn declare_subscription(
    session: &CurrentSession,
    key: &KeyExpression,
    capacity: ChannelCapacity,
) -> AppResult<ZenohSubscription> {
    let subscriber = session
        .session()
        .declare_subscriber(key.clone())
        .with(FifoChannel::new(capacity.get()))
        .await
        .map_zenoh_error("Zenoh subscription setup failed", "Zenoh subscription setup failed.")?;

    Ok(ZenohSubscription::new(subscriber))
}

pub(crate) async fn declare_liveliness_subscription(
    session: &CurrentSession,
    key: &KeyExpression,
    capacity: ChannelCapacity,
    history: bool,
) -> AppResult<ZenohSubscription> {
    let subscriber = session
        .session()
        .liveliness()
        .declare_subscriber(key.clone())
        .history(history)
        .with(FifoChannel::new(capacity.get()))
        .await
        .map_zenoh_error(
            "Zenoh liveliness subscription setup failed",
            "Zenoh liveliness subscription setup failed.",
        )?;

    Ok(ZenohSubscription::new(subscriber))
}

#[derive(Debug, Clone, Copy)]
struct Deadline {
    expires_at: tokio::time::Instant,
}

impl Deadline {
    fn after(timeout: Duration) -> Self {
        Self { expires_at: tokio::time::Instant::now() + timeout }
    }

    fn remaining(self) -> AppResult<Duration> {
        let now = tokio::time::Instant::now();

        if self.expires_at <= now {
            return Err(AppError::RequestTimeout);
        }

        Ok(self.expires_at - now)
    }

    async fn timeout<F, T>(self, future: F) -> AppResult<T>
    where
        F: Future<Output = T>,
    {
        tokio::time::timeout(self.remaining()?, future)
            .await
            .map_err(|_| AppError::RequestTimeout)
    }
}

struct Replies {
    inner: FifoChannelHandler<Reply>,
}

impl Replies {
    fn new(inner: FifoChannelHandler<Reply>) -> Self {
        Self { inner }
    }

    async fn collect(self, deadline: Deadline) -> AppResult<Vec<Reply>> {
        deadline
            .timeout(async move {
                let mut collected = Vec::new();

                while let Ok(reply) = self.inner.recv_async().await {
                    collected.push(reply);
                }

                collected
            })
            .await
    }

    async fn first(self, deadline: Deadline) -> AppResult<Option<Reply>> {
        deadline
            .timeout(async move { self.inner.recv_async().await.ok() })
            .await
    }
}

impl fmt::Debug for Replies {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Replies")
            .finish_non_exhaustive()
    }
}

trait ZenohResultExt<T> {
    fn map_zenoh_error(self, log_message: &'static str, user_message: &'static str)
    -> AppResult<T>;
}

impl<T, E> ZenohResultExt<T> for Result<T, E>
where
    E: fmt::Display,
{
    fn map_zenoh_error(
        self,
        log_message: &'static str,
        user_message: &'static str,
    ) -> AppResult<T> {
        self.map_err(|source| {
            tracing::debug!(error = %source, "{log_message}");
            AppError::service_unavailable(user_message)
        })
    }
}
