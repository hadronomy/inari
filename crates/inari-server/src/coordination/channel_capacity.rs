use std::num::NonZeroUsize;

use serde::{Deserialize, Serialize};

/// Non-zero capacity for bounded async channels and similar FIFO buffers.
#[repr(transparent)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "usize", into = "usize")]
pub struct ChannelCapacity(NonZeroUsize);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InvalidChannelCapacity {
    Zero,
}

impl ChannelCapacity {
    #[must_use]
    pub const fn new(capacity: NonZeroUsize) -> Self {
        Self(capacity)
    }

    #[must_use]
    pub const fn get(self) -> usize {
        self.0.get()
    }
}

impl From<NonZeroUsize> for ChannelCapacity {
    fn from(value: NonZeroUsize) -> Self {
        Self::new(value)
    }
}

impl From<ChannelCapacity> for NonZeroUsize {
    fn from(capacity: ChannelCapacity) -> Self {
        capacity.0
    }
}

impl From<ChannelCapacity> for usize {
    fn from(capacity: ChannelCapacity) -> Self {
        capacity.get()
    }
}

impl TryFrom<usize> for ChannelCapacity {
    type Error = InvalidChannelCapacity;

    fn try_from(value: usize) -> Result<Self, Self::Error> {
        NonZeroUsize::new(value)
            .map(Self)
            .ok_or(InvalidChannelCapacity::Zero)
    }
}

impl std::fmt::Display for ChannelCapacity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

impl std::fmt::Display for InvalidChannelCapacity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Zero => f.write_str("channel capacity must be greater than zero"),
        }
    }
}

impl std::error::Error for InvalidChannelCapacity {}

#[cfg(test)]
mod tests {
    use serde::{Deserialize, Serialize};

    use super::{ChannelCapacity, InvalidChannelCapacity};

    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    struct Wrapper {
        capacity: ChannelCapacity,
    }

    #[test]
    fn rejects_zero_capacities() {
        assert_eq!(ChannelCapacity::try_from(0), Err(InvalidChannelCapacity::Zero));
    }

    #[test]
    fn serde_round_trips_as_a_scalar() {
        let serialized = toml::to_string(&Wrapper {
            capacity: ChannelCapacity::try_from(8).expect("non-zero capacity should be valid"),
        })
        .expect("wrapper should serialize");

        assert_eq!(serialized.trim(), "capacity = 8");

        let parsed: Wrapper = toml::from_str("capacity = 8").expect("wrapper should deserialize");

        assert_eq!(
            parsed.capacity,
            ChannelCapacity::try_from(8).expect("non-zero capacity should be valid")
        );
    }
}
