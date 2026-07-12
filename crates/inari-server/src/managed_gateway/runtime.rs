use inari_gateway::protocol::AgentPublication;
use zenoh::bytes::Encoding;
use zenoh::sample::SampleKind;

use super::ManagedGatewayController;
use super::models::CommandHistoryResponse;
use crate::error::{AppError, AppResult};
use crate::shutdown::ShutdownCoordinator;
use crate::zenoh::CurrentSession;

impl ManagedGatewayController {
    pub async fn run_data_plane(self, shutdown: ShutdownCoordinator) -> AppResult<()> {
        if !self.inner.config.enabled || !self.inner.zenoh_config.enabled {
            shutdown.wait_for_shutdown().await;
            return Ok(());
        }

        let mut sessions = self.inner.zenoh.subscribe_session();

        loop {
            let current = sessions.borrow_and_update().clone();
            let Some(session) = current else {
                tokio::select! {
                    _ = shutdown.wait_for_shutdown() => return Ok(()),
                    changed = sessions.changed() => {
                        if changed.is_err() {
                            return Err(AppError::service_unavailable(
                                "Zenoh session supervisor stopped.",
                            ));
                        }
                    },
                }
                continue;
            };
            let workers = self.run_session_workers(session);
            tokio::pin!(workers);
            tokio::select! {
                _ = shutdown.wait_for_shutdown() => return Ok(()),
                changed = sessions.changed() => {
                    if changed.is_err() {
                        return Err(AppError::service_unavailable(
                            "Zenoh session supervisor stopped.",
                        ));
                    }
                },
                result = &mut workers => {
                    if let Err(error) = result {
                        tracing::warn!(error = %error, "managed gateway Zenoh workers stopped");
                    }
                    sessions.changed().await.map_err(|_| {
                        AppError::service_unavailable("Zenoh session supervisor stopped.")
                    })?;
                },
            }
        }
    }

    async fn run_session_workers(&self, session: CurrentSession) -> AppResult<()> {
        let history_key = self.history_query_key()?;
        let publications_key = self.publications_key()?;
        let history_queryable = session
            .session()
            .declare_queryable(history_key.as_str())
            .await
            .map_err(|source| {
                AppError::service_unavailable(format!(
                    "Failed to declare managed command history queryable: {source}"
                ))
            })?;
        let publication_subscriber = session
            .session()
            .declare_subscriber(publications_key.as_str())
            .await
            .map_err(|source| {
                AppError::service_unavailable(format!(
                    "Failed to declare managed publication subscriber: {source}"
                ))
            })?;

        loop {
            tokio::select! {
                query = history_queryable.recv_async() => {
                    let query = query.map_err(|source| {
                        AppError::service_unavailable(format!(
                            "Managed command history queryable stopped: {source}"
                        ))
                    })?;
                    let key = query.key_expr().as_str().to_owned();
                    let from_sequence = query
                        .parameters()
                        .get("from_sequence")
                        .or_else(|| query.parameters().get("from"))
                        .and_then(|value| value.parse::<u64>().ok())
                        .unwrap_or(1);
                    match self.history_for_query_key(&key, from_sequence).await {
                        Ok(Some(payload)) => {
                            let bytes = serde_json::to_vec(&payload)?;
                            if let Err(error) = query
                                .reply(key.as_str(), bytes)
                                .encoding(Encoding::APPLICATION_JSON)
                                .await
                            {
                                tracing::debug!(error = %error, "failed to reply to command history query");
                            }
                        },
                        Ok(None) => {
                            if let Err(error) = query.reply_err("unknown managed gateway namespace").await {
                                tracing::debug!(error = %error, "failed to reply to unknown history query");
                            }
                        },
                        Err(error) => {
                            if let Err(reply_error) = query.reply_err(error.to_string()).await {
                                tracing::debug!(error = %reply_error, "failed to reply to failed history query");
                            }
                        },
                    }
                },
                sample = publication_subscriber.recv_async() => {
                    let sample = sample.map_err(|source| {
                        AppError::service_unavailable(format!(
                            "Managed publication subscriber stopped: {source}"
                        ))
                    })?;
                    if sample.kind() != SampleKind::Put {
                        continue;
                    }
                    let key = sample.key_expr().as_str().to_owned();
                    let bytes = sample.payload().to_bytes();
                    match serde_json::from_slice::<AgentPublication>(bytes.as_ref()) {
                        Ok(message) => {
                            if let Err(error) = self.record_publication_from_key(&key, message).await {
                                tracing::debug!(error = %error, key, "failed to record agent publication");
                            }
                        },
                        Err(error) => {
                            tracing::debug!(error = %error, key, "ignored non-JSON agent publication");
                        },
                    }
                },
            }
        }
    }

    async fn history_for_query_key(
        &self,
        key: &str,
        from_sequence: u64,
    ) -> AppResult<Option<CommandHistoryResponse>> {
        let Some(agent_id) = self.agent_id_from_key(key) else {
            return Ok(None);
        };
        self.inner
            .store
            .command_history(&agent_id, from_sequence)
            .await
            .map(Some)
    }

    async fn record_publication_from_key(
        &self,
        key: &str,
        message: AgentPublication,
    ) -> AppResult<()> {
        let Some(agent_id) = self.agent_id_from_key(key) else {
            return Ok(());
        };
        self.inner
            .store
            .record_publication(agent_id, key.to_owned(), message)
            .await
    }
}
