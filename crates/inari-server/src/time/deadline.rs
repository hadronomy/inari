use std::future::Future;
use std::time::Duration;

use crate::error::{AppError, AppResult};

/// Relative deadline helper for request-scoped asynchronous operations.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Deadline {
    expires_at: tokio::time::Instant,
}

impl Deadline {
    #[must_use]
    pub(crate) fn after(timeout: Duration) -> Self {
        Self { expires_at: tokio::time::Instant::now() + timeout }
    }

    pub(crate) fn remaining(self) -> AppResult<Duration> {
        let now = tokio::time::Instant::now();

        if self.expires_at <= now {
            return Err(AppError::RequestTimeout);
        }

        Ok(self.expires_at - now)
    }

    pub(crate) async fn timeout<F, T>(self, future: F) -> AppResult<T>
    where
        F: Future<Output = T>,
    {
        tokio::time::timeout(self.remaining()?, future)
            .await
            .map_err(|_| AppError::RequestTimeout)
    }
}
