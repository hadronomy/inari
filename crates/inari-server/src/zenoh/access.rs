use std::fmt;
use std::time::Duration;

use bytes::Bytes;
use zenoh::bytes::{Encoding, ZBytes};
use zenoh::handlers::{FifoChannel, FifoChannelHandler};
use zenoh::pubsub::Subscriber;
use zenoh::query::{QueryConsolidation, Reply, Selector};
use zenoh::sample::Sample;

use super::{CurrentSession, KeyExpression};
use crate::coordination::ChannelCapacity;
use crate::error::{AppError, AppResult};
use crate::time::Deadline;

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
