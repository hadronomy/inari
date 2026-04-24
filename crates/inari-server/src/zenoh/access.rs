use std::{fmt, time::Duration};

use ::zenoh::{
    bytes::Encoding,
    handlers::{FifoChannel, FifoChannelHandler},
    pubsub::Subscriber,
    query::{QueryConsolidation, Reply, Selector},
    sample::Sample,
};
use bytes::Bytes;

use crate::error::{AppError, AppResult};

use super::KeyExpression;

#[derive(Clone)]
pub(crate) struct SessionLease {
    session: ::zenoh::Session,
    zid: String,
    generation: u64,
}

impl SessionLease {
    pub(crate) fn new(session: ::zenoh::Session, zid: String, generation: u64) -> Self {
        Self { session, zid, generation }
    }

    pub(crate) fn session(&self) -> &::zenoh::Session {
        &self.session
    }

    pub(crate) fn zid(&self) -> &str {
        &self.zid
    }
}

impl fmt::Debug for SessionLease {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("SessionLease")
            .field("zid", &self.zid)
            .field("generation", &self.generation)
            .finish_non_exhaustive()
    }
}

#[derive(Debug, Clone)]
pub(crate) struct ZenohQueryRequest {
    selector: Selector<'static>,
    payload: Option<(Bytes, Encoding)>,
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

    pub(crate) fn with_payload(mut self, payload: Bytes, encoding: Encoding) -> Self {
        self.payload = Some((payload, encoding));
        self
    }
}

#[derive(Debug)]
pub(crate) enum SupervisorSignal {
    SessionFault { message: String },
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
        self.subscriber.recv_async().await.map_err(|source| {
            tracing::debug!(error = %source, "Zenoh subscription stream closed");
            AppError::service_unavailable("Zenoh subscription stream is no longer available.")
        })
    }
}

pub(crate) async fn execute_query_collect(
    session: &SessionLease,
    request: ZenohQueryRequest,
) -> AppResult<Vec<Reply>> {
    let timeout = request.timeout;
    let replies = issue_query(session, request).await?;
    collect_replies(replies, timeout).await
}

pub(crate) async fn execute_query_first(
    session: &SessionLease,
    request: ZenohQueryRequest,
) -> AppResult<Option<Reply>> {
    let timeout = request.timeout;
    let replies = issue_query(session, request).await?;
    first_reply(replies, timeout).await
}

pub(crate) async fn execute_liveliness_get_collect(
    session: &SessionLease,
    key: &KeyExpression,
    timeout: Duration,
) -> AppResult<Vec<Reply>> {
    let replies = issue_liveliness_get(session, key, timeout).await?;
    collect_replies(replies, timeout).await
}

pub(crate) async fn execute_liveliness_get_first(
    session: &SessionLease,
    key: &KeyExpression,
    timeout: Duration,
) -> AppResult<Option<Reply>> {
    let replies = issue_liveliness_get(session, key, timeout).await?;
    first_reply(replies, timeout).await
}

async fn issue_query(
    session: &SessionLease,
    request: ZenohQueryRequest,
) -> AppResult<FifoChannelHandler<Reply>> {
    let mut query = session
        .session()
        .get(request.selector)
        .consolidation(request.consolidation)
        .timeout(request.timeout);

    if let Some((payload, encoding)) = request.payload {
        query = query.payload(::zenoh::bytes::ZBytes::from(payload)).encoding(encoding);
    }

    query.await.map_err(|source| {
        tracing::debug!(error = %source, "Zenoh query failed");
        AppError::service_unavailable("Zenoh query failed.")
    })
}

async fn issue_liveliness_get(
    session: &SessionLease,
    key: &KeyExpression,
    timeout: Duration,
) -> AppResult<FifoChannelHandler<Reply>> {
    session.session().liveliness().get(key.clone()).timeout(timeout).await.map_err(|source| {
        tracing::debug!(error = %source, "Zenoh liveliness query failed");
        AppError::service_unavailable("Zenoh liveliness query failed.")
    })
}

pub(crate) async fn declare_subscription(
    session: &SessionLease,
    key: &KeyExpression,
    buffer: usize,
) -> AppResult<ZenohSubscription> {
    let subscriber = session
        .session()
        .declare_subscriber(key.clone())
        .with(FifoChannel::new(buffer))
        .await
        .map_err(|source| {
            tracing::debug!(error = %source, "Zenoh subscription setup failed");
            AppError::service_unavailable("Zenoh subscription setup failed.")
        })?;

    Ok(ZenohSubscription::new(subscriber))
}

pub(crate) async fn declare_liveliness_subscription(
    session: &SessionLease,
    key: &KeyExpression,
    buffer: usize,
    history: bool,
) -> AppResult<ZenohSubscription> {
    let subscriber = session
        .session()
        .liveliness()
        .declare_subscriber(key.clone())
        .history(history)
        .with(FifoChannel::new(buffer))
        .await
        .map_err(|source| {
            tracing::debug!(error = %source, "Zenoh liveliness subscription setup failed");
            AppError::service_unavailable("Zenoh liveliness subscription setup failed.")
        })?;

    Ok(ZenohSubscription::new(subscriber))
}

async fn collect_replies(
    replies: FifoChannelHandler<Reply>,
    timeout: Duration,
) -> AppResult<Vec<Reply>> {
    tokio::time::timeout(timeout, async move {
        let mut collected = Vec::new();

        while let Ok(reply) = replies.recv_async().await {
            collected.push(reply);
        }

        collected
    })
    .await
    .map_err(|_| AppError::RequestTimeout)
}

async fn first_reply(
    replies: FifoChannelHandler<Reply>,
    timeout: Duration,
) -> AppResult<Option<Reply>> {
    tokio::time::timeout(timeout, async move { replies.recv_async().await.ok() })
        .await
        .map_err(|_| AppError::RequestTimeout)
}
