use std::fmt;
use std::time::Duration;

use bytes::Bytes;
use tokio::sync::{broadcast, mpsc, oneshot, watch};
use zenoh::bytes::Encoding;
use zenoh::query::Reply;

use super::access::{
    ZenohQueryRequest, ZenohSubscription, declare_liveliness_subscription, declare_subscription,
    execute_liveliness_get_collect, execute_liveliness_get_first, execute_query_collect,
    execute_query_first,
};
use super::command::Command;
use super::{CurrentSession, KeyExpression, ZenohEvent, ZenohStatus};
use crate::coordination::ChannelCapacity;
use crate::error::{AppError, AppResult};
use crate::zenoh::supervisor::SupervisorSignal;

#[derive(Clone)]
pub struct ZenohHandle {
    pub(super) commands: mpsc::Sender<Command>,
    pub(super) signals: mpsc::Sender<SupervisorSignal>,
    status: watch::Receiver<ZenohStatus>,
    session: watch::Receiver<Option<CurrentSession>>,
    events: broadcast::Sender<ZenohEvent>,
}

impl ZenohHandle {
    pub(super) fn new(
        commands: mpsc::Sender<Command>,
        signals: mpsc::Sender<SupervisorSignal>,
        status: watch::Receiver<ZenohStatus>,
        session: watch::Receiver<Option<CurrentSession>>,
        events: broadcast::Sender<ZenohEvent>,
    ) -> Self {
        Self { commands, signals, status, session, events }
    }

    #[must_use]
    pub fn status_snapshot(&self) -> ZenohStatus {
        self.status.borrow().clone()
    }

    #[must_use]
    pub fn subscribe_status(&self) -> watch::Receiver<ZenohStatus> {
        self.status.clone()
    }

    pub fn subscribe_events(&self) -> broadcast::Receiver<ZenohEvent> {
        self.events.subscribe()
    }

    pub async fn snapshot(&self) -> AppResult<ZenohStatus> {
        Ok(self.status_snapshot())
    }

    pub async fn publish_bytes(&self, key: KeyExpression, payload: Bytes) -> AppResult<()> {
        self.put_bytes(key, payload, Encoding::default(), None)
            .await
    }

    pub async fn put_bytes(
        &self,
        key: KeyExpression,
        payload: Bytes,
        encoding: Encoding,
        attachment: Option<Bytes>,
    ) -> AppResult<()> {
        self.send_unit_command(|respond_to| Command::Publish {
            key,
            payload,
            encoding,
            attachment,
            respond_to,
        })
        .await
    }

    pub async fn delete(&self, key: KeyExpression) -> AppResult<()> {
        self.send_unit_command(|respond_to| Command::Delete { key, respond_to })
            .await
    }

    pub(crate) async fn query(&self, request: ZenohQueryRequest) -> AppResult<Vec<Reply>> {
        let session = self.connected_session()?;

        self.finish_session_operation(execute_query_collect(&session, request).await)
    }

    pub(crate) async fn query_first(&self, request: ZenohQueryRequest) -> AppResult<Option<Reply>> {
        let session = self.connected_session()?;

        self.finish_session_operation(execute_query_first(&session, request).await)
    }

    pub(crate) async fn liveliness_query(
        &self,
        key: &KeyExpression,
        timeout: Duration,
    ) -> AppResult<Vec<Reply>> {
        let session = self.connected_session()?;

        self.finish_session_operation(execute_liveliness_get_collect(&session, key, timeout).await)
    }

    pub(crate) async fn liveliness_query_first(
        &self,
        key: &KeyExpression,
        timeout: Duration,
    ) -> AppResult<Option<Reply>> {
        let session = self.connected_session()?;

        self.finish_session_operation(execute_liveliness_get_first(&session, key, timeout).await)
    }

    pub(crate) async fn subscribe(
        &self,
        key: &KeyExpression,
        capacity: ChannelCapacity,
    ) -> AppResult<ZenohSubscription> {
        let session = self.connected_session()?;

        self.finish_session_operation(declare_subscription(&session, key, capacity).await)
    }

    pub(crate) async fn subscribe_liveliness(
        &self,
        key: &KeyExpression,
        capacity: ChannelCapacity,
        history: bool,
    ) -> AppResult<ZenohSubscription> {
        let session = self.connected_session()?;

        self.finish_session_operation(
            declare_liveliness_subscription(&session, key, capacity, history).await,
        )
    }

    #[must_use]
    pub(crate) fn session_snapshot(&self) -> Option<CurrentSession> {
        self.session.borrow().clone()
    }

    pub(crate) fn connected_session(&self) -> AppResult<CurrentSession> {
        self.session_snapshot()
            .ok_or_else(|| AppError::service_unavailable("Zenoh session is not connected."))
    }

    async fn send_unit_command(
        &self,
        command: impl FnOnce(oneshot::Sender<AppResult<()>>) -> Command,
    ) -> AppResult<()> {
        let (respond_to, response) = oneshot::channel();

        self.commands
            .send(command(respond_to))
            .await
            .map_err(|_| {
                AppError::service_unavailable("Zenoh supervisor is not accepting requests.")
            })?;

        response.await.map_err(|_| {
            AppError::service_unavailable("Zenoh supervisor stopped before completing the request.")
        })?
    }

    fn report_session_fault(&self, error: &AppError) {
        if let Err(send_error) = self
            .signals
            .try_send(SupervisorSignal::OperationFailed { message: error.to_string() })
        {
            tracing::trace!(error = %send_error, "failed to report Zenoh session fault");
        }
    }

    fn finish_session_operation<T>(&self, result: AppResult<T>) -> AppResult<T> {
        if let Err(error) = &result
            && Self::should_report_fault(error)
        {
            self.report_session_fault(error);
        }

        result
    }

    fn should_report_fault(error: &AppError) -> bool {
        matches!(error, AppError::ServiceUnavailable { .. })
    }
}

impl fmt::Debug for ZenohHandle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ZenohHandle")
            .field("status", &self.status_snapshot())
            .finish_non_exhaustive()
    }
}
