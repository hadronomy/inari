use std::num::NonZeroU64;

/// Monotonic open-attempt counter for the embedded Zenoh session lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub(super) struct Attempt(NonZeroU64);

impl Attempt {
    #[must_use]
    pub(super) fn is_first(self) -> bool {
        self.0.get() == 1
    }
}

impl From<Attempt> for u64 {
    fn from(attempt: Attempt) -> Self {
        attempt.0.get()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub(super) struct AttemptCounter {
    current: u64,
}

impl AttemptCounter {
    #[must_use]
    pub(super) const fn new() -> Self {
        Self { current: 0 }
    }

    pub(super) fn next(&mut self) -> Attempt {
        self.current = self.current.saturating_add(1).max(1);

        let value = NonZeroU64::new(self.current)
            .expect("attempt counter is always non-zero after increment");

        Attempt(value)
    }
}
