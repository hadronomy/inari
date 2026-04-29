/// Monotonic generation token used to distinguish successive embedded Zenoh
/// sessions over the lifetime of the process.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub(crate) struct SessionGeneration(u64);

impl SessionGeneration {
    pub(crate) const ZERO: Self = Self(0);

    #[must_use]
    pub(crate) fn next(self) -> Self {
        Self(self.0.saturating_add(1))
    }
}

impl From<SessionGeneration> for u64 {
    fn from(generation: SessionGeneration) -> Self {
        generation.0
    }
}

impl From<SessionGeneration> for String {
    fn from(generation: SessionGeneration) -> Self {
        generation.0.to_string()
    }
}
