use std::num::NonZeroUsize;

use serde::{Deserialize, Serialize};
use tokio::sync::Semaphore;

/// Non-zero concurrency bound used for semaphore-backed request budgets.
#[repr(transparent)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "usize", into = "usize")]
pub struct ConcurrencyLimit(NonZeroUsize);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvalidConcurrencyLimit {
    Zero,
    ExceedsMaximum { max: usize },
}

impl ConcurrencyLimit {
    pub const MAX: usize = Semaphore::MAX_PERMITS;

    pub fn new(limit: NonZeroUsize) -> Result<Self, InvalidConcurrencyLimit> {
        Self::try_from(limit)
    }

    #[must_use]
    pub const fn get(self) -> usize {
        self.0.get()
    }
}

impl TryFrom<NonZeroUsize> for ConcurrencyLimit {
    type Error = InvalidConcurrencyLimit;

    fn try_from(value: NonZeroUsize) -> Result<Self, Self::Error> {
        if value.get() > Self::MAX {
            return Err(InvalidConcurrencyLimit::ExceedsMaximum { max: Self::MAX });
        }

        Ok(Self(value))
    }
}

impl From<ConcurrencyLimit> for NonZeroUsize {
    fn from(limit: ConcurrencyLimit) -> Self {
        limit.0
    }
}

impl From<ConcurrencyLimit> for usize {
    fn from(limit: ConcurrencyLimit) -> Self {
        limit.get()
    }
}

impl TryFrom<usize> for ConcurrencyLimit {
    type Error = InvalidConcurrencyLimit;

    fn try_from(value: usize) -> Result<Self, Self::Error> {
        let value = NonZeroUsize::new(value).ok_or(InvalidConcurrencyLimit::Zero)?;

        Self::try_from(value)
    }
}

impl std::fmt::Display for ConcurrencyLimit {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl std::fmt::Display for InvalidConcurrencyLimit {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Zero => f.write_str("concurrency limit must be greater than zero"),
            Self::ExceedsMaximum { max } => {
                write!(f, "concurrency limit must not exceed {max}")
            },
        }
    }
}

impl std::error::Error for InvalidConcurrencyLimit {}

#[cfg(test)]
mod tests {
    use serde::{Deserialize, Serialize};

    use super::{ConcurrencyLimit, InvalidConcurrencyLimit};

    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    struct Wrapper {
        limit: ConcurrencyLimit,
    }

    #[test]
    fn rejects_zero_limits() {
        assert_eq!(ConcurrencyLimit::try_from(0), Err(InvalidConcurrencyLimit::Zero));
    }

    #[test]
    fn rejects_limits_above_the_semaphore_maximum() {
        assert_eq!(
            ConcurrencyLimit::try_from(ConcurrencyLimit::MAX + 1),
            Err(InvalidConcurrencyLimit::ExceedsMaximum { max: ConcurrencyLimit::MAX }),
        );
    }

    #[test]
    fn serde_round_trips_as_a_scalar() {
        let serialized = toml::to_string(&Wrapper {
            limit: ConcurrencyLimit::try_from(8).expect("non-zero limit should be valid"),
        })
        .expect("wrapper should serialize");

        assert_eq!(serialized.trim(), "limit = 8");

        let parsed: Wrapper = toml::from_str("limit = 8").expect("wrapper should deserialize");

        assert_eq!(
            parsed.limit,
            ConcurrencyLimit::try_from(8).expect("non-zero limit should be valid")
        );
    }
}
