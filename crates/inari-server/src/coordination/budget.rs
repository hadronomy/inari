use std::fmt;
use std::marker::PhantomData;
use std::sync::Arc;

use tokio::sync::{OwnedSemaphorePermit, Semaphore, TryAcquireError};

use super::{ConcurrencyLimit, sealed};
use crate::error::AppError;

pub(crate) trait BudgetKind: sealed::Sealed + 'static {
    const EXHAUSTED_MESSAGE: &'static str;
    const CLOSED_MESSAGE: &'static str;
}

#[must_use = "dropping the permit immediately releases the reserved capacity"]
pub struct BudgetPermit<K> {
    _permit: OwnedSemaphorePermit,
    _kind: PhantomData<fn() -> K>,
}

impl<K> fmt::Debug for BudgetPermit<K> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("BudgetPermit")
            .field("kind", &std::any::type_name::<K>())
            .finish_non_exhaustive()
    }
}

#[derive(Clone)]
pub(crate) struct Budget<K> {
    limit: ConcurrencyLimit,
    semaphore: Arc<Semaphore>,
    _kind: PhantomData<fn() -> K>,
}

impl<K: BudgetKind> fmt::Debug for Budget<K> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Budget")
            .field("kind", &std::any::type_name::<K>())
            .field("limit", &self.limit())
            .field("available_permits", &self.available_permits())
            .field("closed", &self.is_closed())
            .finish()
    }
}

impl<K: BudgetKind> Budget<K> {
    #[must_use]
    pub(crate) fn new(limit: ConcurrencyLimit) -> Self {
        Self { limit, semaphore: Arc::new(Semaphore::new(limit.into())), _kind: PhantomData }
    }

    #[must_use]
    pub(crate) const fn limit(&self) -> ConcurrencyLimit {
        self.limit
    }

    #[must_use]
    pub(crate) fn available_permits(&self) -> usize {
        self.semaphore.available_permits()
    }

    pub(crate) fn close(&self) {
        self.semaphore.close();
    }

    #[must_use]
    pub(crate) fn is_closed(&self) -> bool {
        self.semaphore.is_closed()
    }

    pub(crate) async fn acquire(&self) -> Result<BudgetPermit<K>, AppError> {
        let permit = self
            .semaphore
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| AppError::service_unavailable(K::CLOSED_MESSAGE))?;

        Ok(BudgetPermit { _permit: permit, _kind: PhantomData })
    }

    pub(crate) fn try_acquire(&self) -> Result<BudgetPermit<K>, AppError> {
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
